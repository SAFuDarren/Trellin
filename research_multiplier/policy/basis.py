"""Typed reads of the versioned, currently admissible research basis."""

def versions(store, role, packet_id):
    rows = store.query(role, """SELECT * FROM rm.get_packet_basis(%s)
                       ORDER BY logical_key, version, item_version_id""", packet_id)
    return [
        {"id": str(row["item_version_id"]), "key": row["logical_key"], "version": row["version"],
         "sha256": row["version_sha256"], "label": row["packet_label"], "body": row["body"]}
        for row in rows
    ]
