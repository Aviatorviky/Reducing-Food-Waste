
from datetime import datetime, timedelta, timezone
from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort
)

from database import (
    init_db, add_waste_record, get_dashboard_stats, get_all_records,
    create_donation, get_donations, get_donation,
    add_recipient, get_recipients, set_recipient_verification,
    review_food_safety, get_recommended_recipients,
    accept_donation, update_donation_status, get_donation_events
)

app = Flask(__name__)
app.config["SECRET_KEY"] = "replace-with-a-random-environment-secret"
init_db()

# India Standard Time for date/time inputs in this college prototype.
IST = timezone(timedelta(hours=5, minutes=30))


def parse_local_datetime(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed


@app.route("/")
def dashboard():
    return render_template("dashboard.html", stats=get_dashboard_stats())


@app.route("/waste", methods=["GET", "POST"])
def waste_tracking():
    if request.method == "POST":
        food_name = request.form.get("food_name", "").strip()

        try:
            prepared = float(request.form.get("prepared_kg", ""))
            served = float(request.form.get("served_kg", ""))
            leftover = float(request.form.get("leftover_kg", ""))

            if not food_name or len(food_name) > 100:
                raise ValueError("Enter a food name of up to 100 characters.")
            if not all(0 <= n < float("inf") for n in (prepared, served, leftover)):
                raise ValueError("Quantities must be finite and non-negative.")
            if prepared <= 0:
                raise ValueError("Prepared quantity must be greater than zero.")
            if served + leftover > prepared + 0.000001:
                raise ValueError("Served plus leftover cannot exceed prepared.")

            add_waste_record(food_name, prepared, served, leftover)
            flash("Food waste record saved.", "success")
            return redirect(url_for("waste_tracking"))
        except ValueError as error:
            flash(str(error), "error")

    return render_template("waste_tracking.html", records=get_all_records())


@app.route("/donations", methods=["GET", "POST"])
def donations():
    if request.method == "POST":
        try:
            data = {
                "food_name": request.form["food_name"].strip(),
                "quantity_kg": float(request.form["quantity_kg"]),
                "prepared_at": parse_local_datetime(
                    request.form["prepared_at"]
                ).isoformat(),
                "storage_details": request.form["storage_details"].strip(),
                "allergens": request.form.get("allergens", "Not specified").strip(),
                "collection_deadline": parse_local_datetime(
                    request.form["collection_deadline"]
                ).isoformat(),
                "pickup_latitude": float(request.form["pickup_latitude"]),
                "pickup_longitude": float(request.form["pickup_longitude"]),
            }

            if not data["food_name"] or len(data["food_name"]) > 100:
                raise ValueError("Enter a valid food name.")
            if not 0 < data["quantity_kg"] < float("inf"):
                raise ValueError("Quantity must be a positive finite number.")
            if not (-90 <= data["pickup_latitude"] <= 90):
                raise ValueError("Latitude must be between -90 and 90.")
            if not (-180 <= data["pickup_longitude"] <= 180):
                raise ValueError("Longitude must be between -180 and 180.")
            if not data["storage_details"]:
                raise ValueError("Enter the storage and handling details.")
            if data["collection_deadline"] <= datetime.now(IST).isoformat():
                raise ValueError("Collection deadline must be in the future.")
            if parse_local_datetime(data["prepared_at"]) > datetime.now(IST):
                raise ValueError("Preparation time cannot be in the future.")

            donation_id = create_donation(data)
            flash(
                f"Listing #{donation_id} created. Safety review is required.",
                "success"
            )
            return redirect(url_for("donation_detail", donation_id=donation_id))

        except (ValueError, KeyError) as error:
            flash(f"Please check your entries: {error}", "error")

    return render_template("donations.html", donations=get_donations())


@app.route("/donations/<int:donation_id>", methods=["GET", "POST"])
def donation_detail(donation_id):
    donation = get_donation(donation_id)
    if not donation:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "")

        try:
            if action == "approve_safety":
                review_food_safety(
                    donation_id, "approved",
                    request.form.get("reviewer", "").strip(),
                    request.form.get("safety_notes", "").strip()
                )
                flash("Safety review recorded. Listing is now available.", "success")

            elif action == "reject_safety":
                review_food_safety(
                    donation_id, "rejected",
                    request.form.get("reviewer", "").strip(),
                    request.form.get("safety_notes", "").strip()
                )
                flash("Listing rejected during safety review.", "success")

            elif action == "accept":
                accept_donation(
                    donation_id, int(request.form["recipient_id"])
                )
                flash("Recipient assigned to this donation.", "success")

            elif action == "picked_up":
                update_donation_status(
                    donation_id, "picked_up", "Collection confirmed by staff"
                )
                flash("Collection recorded.", "success")

            elif action == "completed":
                update_donation_status(
                    donation_id, "completed", "Handover confirmed by staff"
                )
                flash("Donation handover marked complete.", "success")

            else:
                raise ValueError("Unknown action.")

        except (ValueError, KeyError) as error:
            flash(str(error), "error")

        return redirect(url_for("donation_detail", donation_id=donation_id))

    donation = get_donation(donation_id)
    matches = get_recommended_recipients(donation_id)
    recipients = get_recipients()
    events = get_donation_events(donation_id)

    return render_template(
        "donation_detail.html",
        donation=donation,
        matches=matches,
        recipients=recipients,
        events=events,
    )


@app.route("/recipients", methods=["GET", "POST"])
def recipients():
    if request.method == "POST":
        try:
            data = {
                "organization_name": request.form["organization_name"].strip(),
                "organization_type": request.form["organization_type"],
                "contact_name": request.form["contact_name"].strip(),
                "contact_email": request.form["contact_email"].strip(),
                "address": request.form["address"].strip(),
                "latitude": float(request.form["latitude"]),
                "longitude": float(request.form["longitude"]),
                "min_quantity_kg": float(request.form["min_quantity_kg"]),
                "max_quantity_kg": float(request.form["max_quantity_kg"]),
                "pickup_start": request.form["pickup_start"],
                "pickup_end": request.form["pickup_end"],
            }

            if not all(data[key] for key in (
                "organization_name", "contact_name", "contact_email", "address"
            )):
                raise ValueError("Complete all organization contact fields.")
            if data["organization_type"] not in (
                "NGO", "Shelter", "Community kitchen"
            ):
                raise ValueError("Choose an allowed organization type.")
            if not (-90 <= data["latitude"] <= 90):
                raise ValueError("Latitude must be between -90 and 90.")
            if not (-180 <= data["longitude"] <= 180):
                raise ValueError("Longitude must be between -180 and 180.")
            if not (
                0 <= data["min_quantity_kg"] < float("inf")
                and 0 < data["max_quantity_kg"] < float("inf")
                and data["min_quantity_kg"] <= data["max_quantity_kg"]
            ):
                raise ValueError("Check the recipient quantity limits.")

            for field in ("pickup_start", "pickup_end"):
                datetime.strptime(data[field], "%H:%M")

            add_recipient(data)
            flash("Organization registered and awaiting verification.", "success")
            return redirect(url_for("recipients"))

        except (ValueError, KeyError) as error:
            flash(f"Please check the organization details: {error}", "error")

    return render_template("recipients.html", recipients=get_recipients())


@app.post("/recipients/<int:recipient_id>/verify")
def verify_recipient(recipient_id):
    decision = request.form.get("decision", "")
    try:
        set_recipient_verification(recipient_id, decision)
        flash(f"Organization marked {decision}.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("recipients"))


if __name__ == "__main__":
    app.run(debug=True)
