
import math
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "smartbite.db"


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS waste_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                food_name TEXT NOT NULL,
                prepared_kg REAL NOT NULL CHECK(prepared_kg > 0),
                served_kg REAL NOT NULL CHECK(served_kg >= 0),
                leftover_kg REAL NOT NULL CHECK(leftover_kg >= 0),
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(served_kg + leftover_kg <= prepared_kg + 0.000001)
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_name TEXT NOT NULL,
                organization_type TEXT NOT NULL,
                contact_name TEXT NOT NULL,
                contact_email TEXT NOT NULL,
                address TEXT NOT NULL,
                latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
                longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180),
                min_quantity_kg REAL NOT NULL DEFAULT 0
                    CHECK(min_quantity_kg >= 0),
                max_quantity_kg REAL NOT NULL
                    CHECK(max_quantity_kg > 0),
                pickup_start TEXT NOT NULL,
                pickup_end TEXT NOT NULL,
                verification_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(verification_status IN ('pending', 'verified', 'rejected')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS donations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                food_name TEXT NOT NULL,
                quantity_kg REAL NOT NULL CHECK(quantity_kg > 0),
                prepared_at TEXT NOT NULL,
                storage_details TEXT NOT NULL,
                allergens TEXT NOT NULL DEFAULT 'Not specified',
                collection_deadline TEXT NOT NULL,
                pickup_latitude REAL NOT NULL,
                pickup_longitude REAL NOT NULL,
                safety_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(safety_status IN ('pending', 'approved', 'rejected')),
                safety_notes TEXT NOT NULL DEFAULT '',
                safety_verified_by TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending_review'
                    CHECK(status IN (
                        'pending_review', 'available', 'accepted',
                        'picked_up', 'completed', 'rejected', 'expired'
                    )),
                recipient_id INTEGER REFERENCES recipients(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS donation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donation_id INTEGER NOT NULL REFERENCES donations(id),
                event_type TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


# Existing food waste functionality

def add_waste_record(food_name, prepared_kg, served_kg, leftover_kg):
    with get_connection() as con:
        con.execute("""
            INSERT INTO waste_records
                (food_name, prepared_kg, served_kg, leftover_kg)
            VALUES (?, ?, ?, ?)
        """, (food_name, prepared_kg, served_kg, leftover_kg))


def get_dashboard_stats():
    with get_connection() as con:
        row = con.execute("""
            SELECT COUNT(*) AS total_records,
                   COALESCE(SUM(prepared_kg), 0) AS prepared_kg,
                   COALESCE(SUM(served_kg), 0) AS served_kg,
                   COALESCE(SUM(leftover_kg), 0) AS leftover_kg
            FROM waste_records
        """).fetchone()

        recent = con.execute("""
            SELECT * FROM waste_records
            ORDER BY recorded_at DESC, id DESC LIMIT 5
        """).fetchall()

        donated = con.execute("""
            SELECT COALESCE(SUM(quantity_kg), 0)
            FROM donations WHERE status = 'completed'
        """).fetchone()[0]

    prepared, served, leftover = (
        row["prepared_kg"], row["served_kg"], row["leftover_kg"]
    )

    return {
        "total_records": row["total_records"],
        "prepared_kg": prepared,
        "served_kg": served,
        "leftover_kg": leftover,
        "waste_percentage": leftover / prepared * 100 if prepared else 0,
        "utilization_percentage": served / prepared * 100 if prepared else 0,
        "recent_records": recent,
        "donated_kg": donated,
    }


def get_all_records():
    with get_connection() as con:
        return con.execute("""
            SELECT * FROM waste_records
            ORDER BY recorded_at DESC, id DESC
        """).fetchall()


# Recipient management

def add_recipient(data):
    with get_connection() as con:
        cur = con.execute("""
            INSERT INTO recipients (
                organization_name, organization_type, contact_name,
                contact_email, address, latitude, longitude,
                min_quantity_kg, max_quantity_kg,
                pickup_start, pickup_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["organization_name"], data["organization_type"],
            data["contact_name"], data["contact_email"], data["address"],
            data["latitude"], data["longitude"], data["min_quantity_kg"],
            data["max_quantity_kg"], data["pickup_start"], data["pickup_end"]
        ))
        recipient_id = cur.lastrowid
        return recipient_id


def get_recipients():
    with get_connection() as con:
        return con.execute("""
            SELECT * FROM recipients ORDER BY created_at DESC, id DESC
        """).fetchall()


def set_recipient_verification(recipient_id, decision):
    if decision not in ("verified", "rejected"):
        raise ValueError("Invalid verification decision.")

    with get_connection() as con:
        con.execute("""
            UPDATE recipients SET verification_status = ?
            WHERE id = ?
        """, (decision, recipient_id))


# Donation listings

def create_donation(data):
    with get_connection() as con:
        cur = con.execute("""
            INSERT INTO donations (
                food_name, quantity_kg, prepared_at, storage_details,
                allergens, collection_deadline, pickup_latitude,
                pickup_longitude
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["food_name"], data["quantity_kg"], data["prepared_at"],
            data["storage_details"], data["allergens"],
            data["collection_deadline"], data["pickup_latitude"],
            data["pickup_longitude"]
        ))
        donation_id = cur.lastrowid
        con.execute("""
            INSERT INTO donation_events (donation_id, event_type, notes)
            VALUES (?, 'created', 'Awaiting food safety review')
        """, (donation_id,))
        return donation_id


def get_donations():
    with get_connection() as con:
        return con.execute("""
            SELECT d.*, r.organization_name AS recipient_name
            FROM donations d
            LEFT JOIN recipients r ON r.id = d.recipient_id
            ORDER BY d.created_at DESC, d.id DESC
        """).fetchall()


def get_donation(donation_id):
    with get_connection() as con:
        return con.execute("""
            SELECT d.*, r.organization_name AS recipient_name
            FROM donations d
            LEFT JOIN recipients r ON r.id = d.recipient_id
            WHERE d.id = ?
        """, (donation_id,)).fetchone()


def record_event(con, donation_id, event_type, notes=""):
    con.execute("""
        INSERT INTO donation_events (donation_id, event_type, notes)
        VALUES (?, ?, ?)
    """, (donation_id, event_type, notes))


def review_food_safety(donation_id, decision, reviewer, notes):
    if decision not in ("approved", "rejected"):
        raise ValueError("Invalid safety decision.")
    if not reviewer.strip():
        raise ValueError("Reviewer name is required.")

    with get_connection() as con:
        donation = con.execute(
            "SELECT * FROM donations WHERE id = ?", (donation_id,)
        ).fetchone()

        if not donation:
            raise ValueError("Donation not found.")

        if donation["status"] != "pending_review":
            raise ValueError("Only listings awaiting review can be reviewed.")

        con.execute("""
            UPDATE donations
            SET safety_status = ?, safety_notes = ?,
                safety_verified_by = ?,
                status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            decision, notes, reviewer,
            "available" if decision == "approved" else "rejected",
            donation_id
        ))
        record_event(con, donation_id, "safety_" + decision, notes)


# Matching: approximate straight-line distance using the Haversine formula

def distance_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_recommended_recipients(donation_id):
    with get_connection() as con:
        donation = con.execute(
            "SELECT * FROM donations WHERE id = ?", (donation_id,)
        ).fetchone()
        if not donation:
            raise ValueError("Donation not found.")

        if donation["status"] != "available" or donation["safety_status"] != "approved":
            return []

        recipients = con.execute("""
            SELECT * FROM recipients
            WHERE verification_status = 'verified'
              AND min_quantity_kg <= ?
              AND max_quantity_kg >= ?
        """, (
            donation["quantity_kg"], donation["quantity_kg"]
        )).fetchall()

    from datetime import datetime

    now = datetime.now().astimezone()
    deadline = datetime.fromisoformat(donation["collection_deadline"])
    if deadline.tzinfo is None:
        raise ValueError("Collection deadline must include a timezone.")

    if deadline <= now:
        return []

    prepared = datetime.fromisoformat(donation["prepared_at"])
    if prepared.tzinfo is None:
        raise ValueError("Preparation time must include a timezone.")

    results = []
    for recipient in recipients:
        try:
            start = datetime.strptime(recipient["pickup_start"], "%H:%M").time()
            end = datetime.strptime(recipient["pickup_end"], "%H:%M").time()
        except ValueError:
            continue

        local_now = now.timetz().replace(tzinfo=None)
        if not (start <= local_now <= end):
            continue

        distance = distance_km(
            donation["pickup_latitude"], donation["pickup_longitude"],
            recipient["latitude"], recipient["longitude"]
        )

        # Transparent ranking heuristic: nearer recipients rank higher.
        # Quantity fit and pickup availability are eligibility requirements.
        results.append({
            "recipient": recipient,
            "distance_km": round(distance, 2),
            "score": round(100 / (1 + distance), 2),
        })

    return sorted(results, key=lambda item: item["distance_km"])


def accept_donation(donation_id, recipient_id):
    with get_connection() as con:
        donation = con.execute(
            "SELECT * FROM donations WHERE id = ?", (donation_id,)
        ).fetchone()
        recipient = con.execute(
            "SELECT * FROM recipients WHERE id = ?", (recipient_id,)
        ).fetchone()

        if not donation or not recipient:
            raise ValueError("Donation or recipient not found.")
        if donation["status"] != "available":
            raise ValueError("Donation is no longer available.")
        if donation["safety_status"] != "approved":
            raise ValueError("Food safety approval is required.")
        if recipient["verification_status"] != "verified":
            raise ValueError("Recipient is not verified.")

        con.execute("""
            UPDATE donations SET status = 'accepted', recipient_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'available'
        """, (recipient_id, donation_id))
        record_event(con, donation_id, "accepted", recipient["organization_name"])


def update_donation_status(donation_id, new_status, notes=""):
    transitions = {
        "accepted": {"picked_up"},
        "picked_up": {"completed"},
    }

    with get_connection() as con:
        donation = con.execute(
            "SELECT * FROM donations WHERE id = ?", (donation_id,)
        ).fetchone()
        if not donation:
            raise ValueError("Donation not found.")

        if new_status not in transitions.get(donation["status"], set()):
            raise ValueError("Invalid status transition.")

        con.execute("""
            UPDATE donations SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_status, donation_id))
        record_event(con, donation_id, new_status, notes)


def get_donation_events(donation_id):
    with get_connection() as con:
        return con.execute("""
            SELECT * FROM donation_events
            WHERE donation_id = ? ORDER BY id
        """, (donation_id,)).fetchall()
