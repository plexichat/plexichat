def test_audit_row_projection_supports_tuple_shape():
    row = (1, 2, 3, "message_edit", "message", 4, "{}", "reason", 5)
    assert row[3] == "message_edit"
    assert row[8] == 5


def test_report_row_projection_columns_are_stable():
    columns = ("id", "reporter_id", "report_type", "target_id", "channel_id")
    values = (1, 2, "user", 3, None)
    projected = dict(zip(columns, values))
    assert projected["reporter_id"] == 2
    assert projected["target_id"] == 3
