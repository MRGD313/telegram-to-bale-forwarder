"""Verify queue DB after from-zero E2E test. Usage: py scripts/verify_test_queue.py public|private"""
import sqlite3
import sys

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))


def check(db_path, profile, expect_topic_id=None):
    if expect_topic_id is None:
        expect_topic_id = 4 if profile == "private" else None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT status, COUNT(1) c FROM queue GROUP BY status ORDER BY status"
    ).fetchall()
    counts = {r["status"]: r["c"] for r in rows}
    total = sum(counts.values())
    print(f"\n=== {profile} ({db_path}) ===")
    print("Counts:", counts, "total=", total)

    failed = conn.execute(
        "SELECT tg_message_id, topic_id, last_error FROM queue WHERE status='failed' LIMIT 5"
    ).fetchall()
    if failed:
        print("FAILED samples:")
        for r in failed:
            print(f"  msg={r['tg_message_id']} topic={r['topic_id']} err={r['last_error'][:120]}")

    pending = counts.get("pending", 0)
    sent = counts.get("sent", 0)
    skipped = counts.get("skipped", 0)
    failed_n = counts.get("failed", 0)

    order_ok = True
    if sent > 1:
        sent_rows = conn.execute(
            """
            SELECT tg_message_id, msg_date, topic_id
            FROM queue WHERE status='sent'
            ORDER BY COALESCE(msg_date,''), tg_message_id
            """
        ).fetchall()
        ids = [r["tg_message_id"] for r in sent_rows]
        order_ok = ids == sorted(ids)
        print(f"Sent message id order ascending: {order_ok} (first={ids[:3]} last={ids[-3:]})")

    bad_topic = 0
    if profile == "private" and expect_topic_id is not None:
        bad_topic = conn.execute(
            "SELECT COUNT(1) FROM queue WHERE status IN ('sent','pending','failed') AND topic_id != ?",
            (expect_topic_id,),
        ).fetchone()[0]
        print(f"Non-topic-{expect_topic_id} active rows (should be 0): {bad_topic}")

    conn.close()
    ok = failed_n == 0 and pending == 0 and order_ok
    if profile == "private":
        ok = ok and bad_topic == 0
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    profile = sys.argv[1] if len(sys.argv) > 1 else "public"
    db = "state_test_public.db" if profile == "public" else "state_test_private.db"
    path = __import__("os").path.join(ROOT, db)
    sys.exit(check(path, profile))


if __name__ == "__main__":
    main()
