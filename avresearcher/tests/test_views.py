from avresearcher.views import _date_table, _find_qstring, _gen_csv_filename

from nose.tools import assert_equal


def test_csv():
    def make_bucket(point):
        # Simulate ES date aggregation format.
        year, count = point
        date = "%d-01-01T00:00:00.000Z" % year
        return {"key_as_string": date, "key": "ignored", "doc_count": count}

    expect1 = [(2000, 4), (2001, 7), (2003, 215), (2004, 1)]
    expect2 = [(1999, 13), (2000, 8), (2002, 2), (2004, 10), (2009, 11)]

    buckets1 = map(make_bucket, expect1 + [(2005, 0)])
    buckets2 = map(make_bucket, [(1998, 0)] + expect2)

    def table(x, y):
        return list(_date_table(x, y))

    assert_equal(table(buckets1, []), expect1)
    assert_equal(table([], buckets1), expect1)
    assert_equal(table(buckets2, []), expect2)
    assert_equal(table([], buckets2), expect2)

    expect_combined = [(1999, 0, 13),
                       (2000, 4, 8),
                       (2001, 7, 0),
                       (2002, 0, 2),
                       (2003, 215, 0),
                       (2004, 1, 10),
                       (2009, 0, 11)]

    assert_equal(table(buckets1, buckets2), expect_combined)


def test_find_qstring():
    q = {u'filtered': {u'filter': {},
          u'query': {u'bool': {u'minimum_should_match': 1,
            u'should': [{u'query_string': {u'default_operator': u'AND',
               u'fields': [u'title', u'text_content'],
               u'query': u'depth-first search'}}]}}}}

    assert_equal(_find_qstring(q), "depth-first search")


def test_gen_csv_filename():
    p1 = {u'aggs': {u'dates': {u'date_histogram': {u'field': u'paper_dc_date',
             u'interval': u'year',
             u'min_doc_count': 0}}},
          u'index': u'kb',
          u'query': {u'filtered': {u'filter': {},
            u'query': {u'bool': {u'minimum_should_match': 1,
              u'should': [{u'query_string': {u'default_operator': u'AND',
                 u'fields': [u'article_dc_title', u'text_content'],
                 u'query': u'waarom'}}]}}}}}

    p2 = {u'aggs': {u'dates': {u'date_histogram': {u'field': u'paper_dc_date',
             u'interval': u'year',
             u'min_doc_count': 0}}},
          u'index': u'kb',
          u'query': {u'filtered': {u'filter': {},
            u'query': {u'bool': {u'minimum_should_match': 1,
              u'should': [{u'query_string': {u'default_operator': u'AND',
                 u'fields': [u'article_dc_title', u'text_content'],
                 u'query': u''}}]}}}}}

    assert_equal("kb_waarom", _gen_csv_filename(p1, p2))
    assert_equal("kb_waarom", _gen_csv_filename(p2, p1))
    assert_equal("kb_waarom_kb_waarom", _gen_csv_filename(p1, p1))
