from flask import Flask, render_template, request, redirect, flash, url_for
from pymongo import MongoClient
from datetime import datetime
from bson.objectid import ObjectId

app = Flask(__name__)
app.secret_key = 'supersecretkey_parking_system'

client = MongoClient("mongodb://localhost:27017/")
db = client["parking_db"]
collection = db["vehicles"]
TOTAL_SLOTS = 10

@app.route('/', methods=["GET", "POST"])
def index():
    search_query = request.args.get('search', '').strip()
    active_query = {"exit_time": None}

    if search_query:
        active_query["$or"] = [
            {"owner_name": {"$regex": search_query, "$options": "i"}},
            {"vehicle_number": {"$regex": search_query, "$options": "i"}}
        ]

    vehicles = list(collection.find(active_query))
    for v in vehicles:
        v["_id"] = str(v["_id"])

    slots = {v["slot_number"]: v for v in vehicles}
    parked_count = len(vehicles)
    available = TOTAL_SLOTS - parked_count

    exited = list(collection.find({"exit_time": {"$ne": None}}))
    total_income = sum(v.get("parking_fee", 0) for v in exited)
    exited_count = len(exited)

    if request.method == "POST":
        owner = request.form["owner"].strip()
        vnumber = request.form["vnumber"].strip()
        vtype = request.form["vtype"].strip()
        slot_raw = request.form["slot"].strip()

        if not all([owner, vnumber, vtype, slot_raw]):
            flash("🚫 All fields are required.", "danger")
            return redirect(url_for('index'))

        try:
            slot = int(slot_raw)
            if not (1 <= slot <= TOTAL_SLOTS):
                flash(f"🚫 Invalid slot (1–{TOTAL_SLOTS}).", "danger")
                return redirect(url_for('index'))
        except:
            flash("🚫 Slot must be a number.", "danger")
            return redirect(url_for('index'))

        if collection.find_one({"slot_number": str(slot), "exit_time": None}):
            flash(f"🚫 Slot {slot} is occupied.", "danger")
            return redirect(url_for('index'))

        if collection.find_one({"vehicle_number": vnumber, "exit_time": None}):
            flash(f"🚫 Vehicle {vnumber} is already parked.", "danger")
            return redirect(url_for('index'))

        entry_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        vehicle = {
            "owner_name": owner,
            "vehicle_number": vnumber,
            "vehicle_type": vtype,
            "slot_number": str(slot),
            "entry_time": entry_time,
            "exit_time": None
        }
        collection.insert_one(vehicle)
        flash(f"✅ {vnumber} parked in Slot {slot}.", "success")
        return redirect(url_for('index'))

    return render_template("index.html",
                           vehicles=vehicles,
                           slots=slots,
                           search_query=search_query,
                           total_parking_slots=TOTAL_SLOTS,
                           parked_vehicles_count=parked_count,
                           available_slots=available,
                           total_income=round(total_income, 2),
                           exited_count=exited_count)

@app.route('/exit/<vehicle_id>')
def exit_vehicle(vehicle_id):
    vehicle = collection.find_one({"_id": ObjectId(vehicle_id)})
    if not vehicle or vehicle.get("exit_time"):
        flash("🚫 Vehicle not found or already exited.", "danger")
        return redirect(url_for('index'))

    entry = datetime.strptime(vehicle["entry_time"], "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    hours = (now - entry).total_seconds() / 3600
    rounded = int(hours) + (1 if hours % 1 > 0 else 0)
    fee = rounded * 2

    collection.update_one(
        {"_id": ObjectId(vehicle_id)},
        {"$set": {
            "exit_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": f"{hours:.2f} hrs",
            "parking_fee": round(fee, 2)
        }}
    )
    flash(f"✅ Vehicle exited — {hours:.2f} hrs, Fee €{fee:.2f}", "info")
    return redirect(url_for('index'))

@app.route('/history')
def history():
    records = list(collection.find({"exit_time": {"$ne": None}}))
    for v in records:
        v["_id"] = str(v["_id"])
        entry = datetime.strptime(v["entry_time"], "%Y-%m-%d %H:%M:%S")
        exit = datetime.strptime(v["exit_time"], "%Y-%m-%d %H:%M:%S")
        duration = (exit - entry).total_seconds() / 3600
        rounded = int(duration) + (1 if duration % 1 > 0 else 0)
        v["parking_fee"] = rounded * 2
        v["duration"] = f"{duration:.2f} hrs"
        v["entry_time_formatted"] = entry.strftime("%Y-%m-%d %H:%M")
        v["exit_time_formatted"] = exit.strftime("%Y-%m-%d %H:%M")

    return render_template("history.html", vehicles=records)

if __name__ == "__main__":
    app.run(debug=True)
