import re
import uuid
import json

from elasticsearch.helpers import bulk
from flask import (Blueprint, current_app, render_template, abort, request,
                   Response, jsonify, url_for)
from flask.ext.login import login_user, logout_user, login_required, current_user
from flask.ext.mail import Message
from slugify import slugify_filename
from toolz import first, get_in, join

from .extensions import db, mail, bcrypt
from .models import User


views = Blueprint('views', __name__)

R_EMAIL = re.compile(r'^.+@[^.].*\.[a-z]{2,10}$', re.IGNORECASE)


@views.route('/', methods=['GET'])
def index():
    exposed_settings = [
        'DEBUG', 'HITS_PER_PAGE', 'ALLOWED_INTERVALS', 'DATE_AGGREGATION',
        'COLLECTIONS_CONFIG', 'ENABLED_COLLECTIONS', 'MINIMUM_CLOUD_FONTSIZE',
        'MAXIMUM_CLOUD_FONTSIZE', 'BARCHART_BARS', 'BARCHART_BAR_HEIGHT',
        'HIT_HIGHLIGHT_FIELDS', 'HIT_HIGHLIGHT_FRAGMENTS',
        'HIT_HIGHLIGHT_FRAGMENT_SIZE', 'LOG_EVENTS', 'ENABLE_USAGE_LOGGING',
        'DATE_STATS_AGGREGATION'
    ]

    settings = {}
    for setting in exposed_settings:
        settings[setting] = current_app.config[setting]

    # Check if user authentication is disabled
    login_disabled = current_app.config.get('LOGIN_DISABLED', False)

    settings['LOGIN_DISABLED'] = login_disabled
    if not login_disabled and current_user.is_authenticated():
        settings['AUTHENTICATED_USER'] = True
        settings['USER'] = {
            'name': current_user.name,
            'organization': current_user.organization,
            'email': current_user.email
        }
    else:
        settings['AUTHENTICATED_USER'] = False
        settings['USER'] = None

    return render_template('index.html', settings=settings)


@views.route('/verify_email/<int:user_id>/<token>')
def verify_email(user_id, token):
    """When visited with a valid `user_id` and `token` combination,
    the user's mailaddress is marked as 'verified'. The person
    responsible for approving accounts will receive an email about the
    new registration with a link to approve the account."""
    user = db.session.query(User)\
        .filter_by(id=user_id, email_verification_token=token).first()

    # If the user_id <-> token combination does not exist, return a 401
    if not user:
        abort(401)

    # Mark mail address as verified
    user.email_verified = True
    db.session.add(user)
    db.session.commit()

    # Send email to mail address responsible for approving accounts
    approve_url = url_for('.approve_user', user_id=user.id,
                          token=user.approval_token, _external=True)

    MESSAGES = current_app.config['MESSAGES']

    msg = Message(MESSAGES['email_approval_subject'],
                  sender=current_app.config['MAIL_DEFAULT_SENDER'],
                  recipients=[current_app.config['MAIL_ACCOUNT_APPROVAL_ADDRESS']])

    msg.body = MESSAGES['email_approval_body'] % (user.name, user.organization,
                                                  user.email, approve_url)
    mail.send(msg)

    messages = {
        'email_verified_title': MESSAGES['email_verified_title'] % user.name,
        'email_verified_content': MESSAGES['email_verified_content']
    }

    return render_template('verify_email.html', **messages)


@views.route('/approve_user/<int:user_id>/<token>')
def approve_user(user_id, token):
    """When visited with a valid `user_id` and `token` combination, the
    user's account is marked as 'approved'. An email notification of
    this approval will be send to the user."""
    user = db.session.query(User)\
                     .filter_by(id=user_id, approval_token=token, email_verified=True)\
                     .first()

    if not user:
        abort(401)

    # Mark account as approved
    user.approved = True
    db.session.add(user)
    db.session.commit()

    MESSAGES = current_app.config['MESSAGES']

    # Notify the user of approval by email
    msg = Message(MESSAGES['email_approved_subject'],
                  sender=current_app.config['MAIL_DEFAULT_SENDER'],
                  recipients=[user.email])

    index_url = url_for('.index', _external=True)
    msg.body = MESSAGES['email_approved_body'] % (user.name, index_url)

    mail.send(msg)

    return render_template('approve_user.html', user=user,
                           user_approved_title=MESSAGES['user_approved_title']
                           % user.name)


@views.route('/api/register', methods=['POST'])
def register():
    """Register a new user account."""
    errors = []

    MESSAGES = current_app.config['MESSAGES']

    # Return errors when required fields are not provided or empty
    required_field = ['name', 'email', 'password']
    for field in required_field:
        if field not in request.form or len(request.form[field].strip()) < 1:
            errors.append(MESSAGES['missing_%s' % field])

    # Verify if the email address is valid
    if not R_EMAIL.match(request.form['email']):
        errors.append(MESSAGES['invalid_email'])

    # Check if there is not already a user with the same mail address
    dupe_mail = db.session.query(User).filter_by(
        email=request.form['email']).first()
    if dupe_mail:
        errors.append(MESSAGES['account_already_exists'])

    if errors:
        return jsonify({'success': False, 'errors': errors})

    # Hash the provided password
    password = bcrypt.generate_password_hash(request.form['password'], 12)

    # Create the user record
    user = User(
        email=request.form['email'],
        password=password,
        name=request.form['name'],
        email_verification_token=str(uuid.uuid4()),
        approval_token=str(uuid.uuid4())
    )

    # Add the user's org., if provided
    if 'organization' in request.form and\
            len(request.form['organization'].strip()) > 1:
        user.organization = request.form['organization'].strip()

    db.session.add(user)
    db.session.commit()

    # Send account activation e-mail
    verification_url = url_for('.verify_email', user_id=user.id,
                               token=user.email_verification_token,
                               _external=True)

    msg = Message(MESSAGES['email_verification_subject'],
                  sender=current_app.config['MAIL_DEFAULT_SENDER'],
                  recipients=[request.form['email']])
    msg.body = MESSAGES['email_verification_body']\
        % (request.form['name'], verification_url)
    mail.send(msg)

    return jsonify({'success': True})


@views.route('/api/login', methods=['POST'])
def login():
    """Login the user."""
    errors = []

    MESSAGES = current_app.config['MESSAGES']

    # Return errors when required fields are not provided or empty
    required_field = ['email', 'password']
    for field in required_field:
        if field not in request.form or len(request.form[field].strip()) < 1:
            errors.append(MESSAGES['missing_%s' % field])

    if errors:
        return jsonify({'success': False, 'errors': errors})

    # Fetch the user from the db
    user = db.session.query(User).filter_by(email=request.form['email']).first()
    if not user:
        return jsonify({'success': False, 'errors': [MESSAGES['login_failed']]})

    # Validate password
    if not bcrypt.check_password_hash(user.password, request.form['password']):
        return jsonify({
            'success': False,
            'errors': [MESSAGES['login_failed']]
        })

    # Check if the user has verified his email address
    if not user.email_verified:
        return jsonify({
            'success': False,
            'errors': [MESSAGES['email_not_verified']]
        })

    # Check if staff already approved this account
    if not user.approved:
        return jsonify({
            'success': False,
            'errors': [MESSAGES['account_not_approved']]
        })

    # This must be a valid user, log the user in!
    login_user(user)

    return jsonify({
        'success': True,
        'user': {
            'name': user.name,
            'organization': user.organization,
            'email': user.email
        }
    })


@views.route('/api/logout', methods=['GET'])
@login_required
def logout():
    """Logs out the user by deleting the session."""
    logout_user()

    return jsonify({'success': True})


@views.route('/api/search', methods=['POST'])
@login_required
def search():
    payload = json.loads(request.form['payload'])

    if isinstance(payload, dict):
        index, payload = _payload_to_es(payload)
        results = current_app.es_search.search(index=index, body=payload)

    elif isinstance(payload, list):
        body = []
        for query in payload:
            index, query = _payload_to_es(query)
            body.append({'index': index})
            body.append(query)

        results = current_app.es_search.msearch(body=body)

    return jsonify(results)


def _payload_to_es(payload):
    """Fills in details of payload that come from COLLECTIONS_CONFIG.

    Returns (actual index name, payload).

    Assumes payload passed in may be modified.
    """
    config = current_app.config['COLLECTIONS_CONFIG']
    index = payload.pop('index')
    index_name = config.get(index)['index_name']

    # If we're sorting on date, we need to know what the date field is called.
    # XXX This should probably be handled in the JavaScript.
    sort_by_date = get_in(['sort', 0, 'date'], payload)
    if sort_by_date and get_in([index, 'date_sort_field'], config):
        sort_spec = payload['sort'][0]
        sort_spec[config[index]['date_sort_field']] = sort_spec.pop('date')

    return index_name, payload


@views.route('/api/count', methods=['POST'])
@login_required
def count():
    payload = json.loads(request.form['payload'])

    index = current_app.config['COLLECTIONS_CONFIG'].get(payload.pop('index'))['index_name']
    query = payload.get('query')

    results = current_app.es_search.count(index=index, body=query)

    return jsonify(results)


@views.route('/api/export', methods=['POST'])
@login_required
def export_cvs():
    payload1, payload2 = json.loads(request.form['payload'])

    filename = _gen_csv_filename(payload1, payload2)

    payload1['size'] = 0
    payload2['size'] = 0

    config = current_app.config['COLLECTIONS_CONFIG']
    index1 = config.get(payload1.pop('index'))['index_name']
    index2 = config.get(payload2.pop('index'))['index_name']

    results1 = current_app.es_search.search(index=index1, body=payload1)
    results2 = current_app.es_search.search(index=index2, body=payload2)

    date_aggr = current_app.config['DATE_AGGREGATION']
    results1 = results1["aggregations"][date_aggr]['buckets']
    results2 = results2["aggregations"][date_aggr]['buckets']

    return Response((",".join(map(str, row)) + "\n"
                     for row in _date_table(results1, results2)),
                    headers={"Content-Disposition":
                             "attachment; filename=%s" % filename},
                    mimetype='text/csv')


def _gen_csv_filename(payload1, payload2):
    # Generate a pretty filename for our CSV dump.
    index1 = payload1['index']
    index2 = payload2['index']

    q1 = _find_qstring(payload1)
    q2 = _find_qstring(payload2)

    if not q1:
        index1 = ""
    if not q2:
        index2 = ""

    return slugify_filename(" ".join(part for part in [index1, q1, index2, q2]
                                          if part)) + '.csv'


def _find_qstring(payload):
    # Find the query_string in haystack. This may look like a brute-force
    # approach, but it's robust against changes in the query structure.

    def dfs(needle, haystack):
        if hasattr(haystack, "iteritems"):
            for k, v in haystack.iteritems():
                if k == needle:
                    yield v
                for r in dfs(needle, v):
                    yield r
        elif isinstance(haystack, (list, tuple)):
            # The check is for (list, tuple) because Iterable matches strings.
            for x in haystack:
                for r in dfs(needle, x):
                    yield r

    payload = next(dfs('query_string', payload))
    return next(dfs('query', payload))


def _date_table(results1, results2):
    def year(bucket):
        return int(bucket['key_as_string'].split('-', 1)[0])

    def generate1(buckets):
        for x in buckets:
            count = x['doc_count']
            if int(count) != 0:
                yield year(x), x['doc_count']

    def generate2(buckets1, buckets2):
        for x, y in join(first, generate1(buckets1), first, generate1(buckets2),
                         left_default=None, right_default=None):
            if x is None:
                yield y[0], 0, y[1]
            elif y is None:
                yield x[0], x[1], 0
            else:
                yield x[0], x[1], y[1]

    if results1 and results2:
        # XXX looks like toolz.join undoes our nice sort order. Too bad.
        return sorted(generate2(results1, results2), key=first)
    elif results1:
        return generate1(results1)
    else:
        return generate1(results2)


@views.route('/api/log_usage', methods=['POST'])
@login_required
def log_usage():
    success = jsonify({'success': True})

    if current_app.es_log is None:
        return success

    user_id = getattr(current_user, 'id', 'anonymous')  # For LOGIN_DISABLED.
    bulk(current_app.es_log,
         _gen_bulk_events(json.loads(request.form['events']),
                          user_id=user_id,
                          log_index=current_app.config['ES_LOG_INDEX']),
         stats_only=True)   # Don't care about errors.

    return success


def _gen_bulk_events(events, user_id, log_index):
    """Updates events (in-place) with user_id + metadata for bulk transfer."""
    metadata = [
        ('_op_type', 'create'),
        ('_index', log_index),
        ('_type', 'event'),
        ('user_id', user_id),
    ]
    for event in events:
        event.update(metadata)
        yield event
