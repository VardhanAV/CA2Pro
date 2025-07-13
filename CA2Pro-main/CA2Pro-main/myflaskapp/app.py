from flask import Flask, render_template, request, redirect, flash, url_for, session
from pymongo import MongoClient
from datetime import datetime
from bson.objectid import ObjectId
import pyotp
import qrcode
from io import BytesIO
import base64

app = Flask(__name__)
app.secret_key = "supersecretkey_parking_system"

client = MongoClient("mongodb://localhost:27017/")
db = client["parking_db"]
collection = db["vehicles"]
users_collection = db["users"]

TOTAL_SLOTS = 10
REGISTRATION_USER = "admin"
REGISTRATION_PASS = "securepass123"

@app.route('/', methods=["GET", "POST"])
def index():
    if not session.get("authenticated_user"):
        return redirect(url_for("login"))

    # 🔄 Parking logic
    if request.method == "POST":
        owner = request.form["owner"].strip()
        vnumber = request.form["vnumber"].strip()
        vtype = request.form["vtype"].strip()
        slot_raw = request.form["slot"].strip()

        if not all([owner, vnumber, vtype, slot_raw]):
            flash("All fields are required.", "danger")
            return redirect(url_for("index"))

        try:
            slot = int(slot_raw)
            if not (1 <= slot <= TOTAL_SLOTS):
                flash(f"Slot must be between 1 and {TOTAL_SLOTS}.", "danger")
                return redirect(url_for("index"))
        except ValueError:
            flash("Slot must be a number.", "danger")
            return redirect(url_for("index"))

        # 🧠 Slot + vehicle conflict check
        existing_vehicle = collection.find_one({"vehicle_number": vnumber, "exit_time": None})
        existing_slot = collection.find_one({"slot_number": str(slot), "exit_time": None})

        if existing_vehicle:
            flash(f"Vehicle {vnumber} is already parked in Slot {existing_vehicle['slot_number']}.", "danger")
            return redirect(url_for("index"))

        if existing_slot:
            flash(f"Slot {slot} is occupied by {existing_slot['vehicle_number']} ({existing_slot['owner_name']}).", "danger")
            return redirect(url_for("index"))

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
        flash(f"{vnumber} parked successfully in Slot {slot}.", "success")
        return redirect(url_for("index"))

    # 🔍 Search handling
    search_query = request.args.get('search', '').strip()
    match = None
    if search_query:
        match = collection.find_one({
            "exit_time": None,
            "$or": [
                {"owner_name": {"$regex": search_query, "$options": "i"}},
                {"vehicle_number": {"$regex": search_query, "$options": "i"}}
            ]
        })
        if match:
            flash(f"Match found: {match['vehicle_number']} in Slot {match['slot_number']}.", "info")
        else:
            flash("No matching vehicle found.", "warning")

    # 🧾 Current vehicle overview
    vehicles = list(collection.find({"exit_time": None}))
    for v in vehicles:
        v["_id"] = str(v["_id"])
    slots = {v["slot_number"]: v for v in vehicles}
    parked_count = len(vehicles)
    available = TOTAL_SLOTS - parked_count

    exited = list(collection.find({"exit_time": {"$ne": None}}))
    total_income = sum(v.get("parking_fee", 0) for v in exited)
    exited_count = len(exited)

    return render_template("index.html",
        vehicles=vehicles,
        slots=slots,
        total_parking_slots=TOTAL_SLOTS,
        parked_vehicles_count=parked_count,
        available_slots=available,
        total_income=round(total_income, 2),
        exited_count=exited_count,
        search_query=search_query
    )

@app.route('/exit/<vehicle_id>')
def exit_vehicle(vehicle_id):
    if not session.get("authenticated_user"):
        return redirect(url_for("login"))

    vehicle = collection.find_one({"_id": ObjectId(vehicle_id)})
    if not vehicle or vehicle.get("exit_time"):
        flash("Vehicle not found or already exited.", "danger")
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
    flash(f"Vehicle exited — {hours:.2f} hrs, Fee €{fee:.2f}", "info")
    return redirect(url_for('index'))

@app.route('/history')
def history():
    if not session.get("authenticated_user"):
        return redirect(url_for("login"))

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

@app.route('/register_gate', methods=["GET", "POST"])
def register_gate():
    if request.method == "POST":
        user = request.form["global_user"].strip()
        password = request.form["global_pass"].strip()
        if user == REGISTRATION_USER and password == REGISTRATION_PASS:
            session["registration_granted"] = True
            return redirect(url_for("register"))
        flash("Invalid global credentials.", "danger")
        return redirect(url_for("register_gate"))
    return render_template("register_gate.html")

@app.route('/register', methods=["GET", "POST"])
def register():
    if not session.get("registration_granted"):
        return redirect(url_for("register_gate"))

    if request.method == "POST":
        username = request.form["username"].strip().lower()
        if users_collection.find_one({"username": username}):
            flash("Username already registered.", "danger")
            return redirect(url_for("register"))

        secret = pyotp.random_base32()
        users_collection.insert_one({
            "username": username,
            "mfa_secret": secret
        })

        uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="Vehicle Parking Tracker")
        qr_img = qrcode.make(uri)
        buf = BytesIO()
        qr_img.save(buf, format='PNG')
        qr_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        session.pop("registration_granted")
        return render_template("registered.html", qr_code=qr_base64, username=username)

    return render_template("register.html")

@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        otp = request.form["otp"].strip()

        user = users_collection.find_one({"username": username})
        if not user:
            flash("User not found.", "danger")
            return redirect(url_for("login"))

        totp = pyotp.TOTP(user["mfa_secret"])
        if totp.verify(otp):
            session["authenticated_user"] = username
            flash(f"Logged in as {username}", "success")
            return redirect(url_for("index"))
        else:
            flash("Invalid OTP code.", "danger")
            return redirect(url_for("login"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)
