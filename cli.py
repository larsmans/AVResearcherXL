#!/usr/bin/env python
import logging
import json
import os
import re
import tarfile
from datetime import datetime
from glob import glob

import click
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import TransportError
from elasticsearch.helpers import bulk

from quamerdes.settings import ES_SEARCH_HOST, ES_SEARCH_PORT

logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('quamerdes')
logger.setLevel(logging.DEBUG)

es = Elasticsearch(host=ES_SEARCH_HOST, port=ES_SEARCH_PORT)


@click.group()
def cli():
    pass


@cli.group()
def elasticsearch():
    pass


@elasticsearch.command('put_template')
@click.argument('templ_file', type=click.File('rb'))
@click.argument('templ_name', default='quamerdes')
def es_put_template(templ_file, templ_name):
    """Upload template"""
    result = es.indices.put_template(name=templ_name, body=json.load(templ_file))

    if result['acknowledged']:
        click.echo('Added template %s' % templ_name)


@elasticsearch.command('delete_template')
@click.argument('templ_name', default='quamerdes')
def es_delete_template(templ_name):
    """Delete template"""
    result = es.indices.delete_template(name=templ_name)

    if result['acknowledged']:
        click.echo('Removed template %s' % templ_name)


@elasticsearch.command('create_indexes')
@click.argument('mapping_dir', type=click.Path(exists=True, file_okay=False,
                                               resolve_path=True))
@click.option('--mapping_prefix', default='mapping_',
              help='The prefix of the mapping files (default)')
def es_create_indexes(mapping_dir, mapping_prefix):
    """Create indexes for all mappings in a directory

    The default prefix of a mapping in 'mapping_dir' is 'mapping_'.
    """
    r_index_name = re.compile(r"%s(.*)\.json$" % mapping_prefix)
    for mapping_file_path in glob(os.path.join(mapping_dir, '%s*' %mapping_prefix)):
        index_name = r_index_name.findall(mapping_file_path)[0]

        click.echo('Creating ES index %s' % index_name)

        mapping_file = open(mapping_file_path, 'rb')
        mapping = json.load(mapping_file)
        mapping_file.close()

        try:
            es.indices.create(index=index_name, body=mapping)
        except TransportError as e:
            click.echo('Creation of ES index %s failed: %s' % (index_name, e))


@elasticsearch.command('index_collection')
@click.argument('name')
@click.argument('files', nargs=-1, type=click.Path(exists=True, resolve_path=True))
def es_index_collection(name, files):
    """Index a given collection

    NAME corressponds to the name of the Elasticsearch index, FILES should
    be one ore more files that contain the collection data.

    \b
    Currently NAME can take the following values:
    - quamerdes_immix
    - quamerdes_kb
    """
    if name == 'quamerdes_immix':
        item_getter = get_immix_items
    elif name == 'quamerdes_kb':
        item_getter = get_kb_items
    else:
        pass

    for f in files:
        actions = es_format_index_actions(name, 'item', item_getter(f))
        bulk(es, actions=actions)


def get_immix_items(archive_path):
    with tarfile.open(archive_path, 'r:gz') as tar:
        for immix_file in tar:
            f = tar.extractfile(immix_file)
            expression = json.load(f)
            doc_id = immix_file.name.split('/')[-1].split('.')[0].lstrip('_')

            # Skip items that don't include a date
            if not expression['date']:
                logger.warn('Skipping iMMix item %s, unknown date' % doc_id)
                yield None
            else:
                yield doc_id, expression


def get_kb_items(archive_path):
    min_date = datetime.strptime('1900-01-01', '%Y-%m-%d')
    publication_name = re.findall(r'.*\/(.*)\.tar.gz$', archive_path)[0]

    publications = {
        'de-tijd-de-maasbode': 'De Tijd / de Maasbode',
        'de-telegraaf': 'De Telegraaf',
        'nieuwsblad-van-het-noorden': 'Nieuwsblad van het Noorden',
        'leeuwarder-courant': 'Leeuwarder Courant',
        'de-waarheid': 'De Waarheid',
        'nieuwsblad-van-friesland-hepkemas-courant': 'Nieuwsblad van Friesland',
        'limburger-koerier-provinciaal-dagblad': 'Limburger koerier',
        'de-volkskrant': 'De Volkskrant',
        'de-tijd-de-maasbode': 'De Tijd De Maasbode'
    }

    publication_name = publications[publication_name]

    with tarfile.open(archive_path, 'r:gz') as tar:
        for kb_file in tar:
            f = tar.extractfile(kb_file)
            doc_id = kb_file.name.split('/')[-1].split('.')[0]

            logger.debug('Processing doc %s' % doc_id)

            article = json.load(f)
            article['meta'] = article.pop('_meta')
            article['meta']['publication_name'] = publication_name

            if not article['date']:
                logger.warn('Skipping KB item %s, unknown date' % doc_id)
                yield None
            else:
                article_date = datetime.strptime(article['date'], '%Y-%m-%d')
                if article_date < min_date:
                    logger.warn('Skipping KB item %s, date before %s'
                                % (doc_id, min_date.isoformat()))
                    yield None
                else:
                   yield doc_id, article

            tar.members = []


def es_format_index_actions(index_name, doc_type, item_iterable):
    for item in item_iterable:
        if not item:
            pass
        else:
            yield {
                '_index': index_name,
                '_type': doc_type,
                '_id': item[0],
                '_source': item[1]
            }


if __name__ == '__main__':
    cli()
