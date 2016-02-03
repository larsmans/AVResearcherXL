from avresearcher.views import _date_table

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
