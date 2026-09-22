"""Role-specific Streamlit demonstration client for the v2 API."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta, timezone

import requests
import streamlit as st
import streamlit.components.v1 as components


API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")
BROWSER_API_BASE = os.getenv("BROWSER_API_BASE_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")

st.set_page_config(page_title="Healthcare AI Platform", page_icon="🏥", layout="wide")

IST = timezone(timedelta(hours=5, minutes=30))


def to_ist(value: str | None) -> str:
    """Render a UTC ISO-8601 timestamp as human-readable 24h IST, e.g. '05 Oct 2026, 14:30 IST'."""
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def time_to_ist_label(value: str | None) -> str:
    """Render a bare 24h time-of-day string (already local clock time, e.g. calendar working hours) as HH:MM."""
    if not value:
        return "—"
    text = str(value)
    return text[:5] if len(text) >= 5 else text

_voice_component = components.declare_component(
    "voice_assistant", path=os.path.join(os.path.dirname(__file__), "voice_component")
)


def api(method: str, path: str, *, data=None, params=None, timeout=15):
    headers = {}
    if st.session_state.get("token"):
        headers["Authorization"] = f"Bearer {st.session_state.token}"
    try:
        response = requests.request(
            method, f"{API_BASE}{path}", headers=headers, json=data,
            params=params, timeout=timeout,
        )
    except requests.RequestException as exc:
        st.error(f"API is unavailable: {exc}")
        return None
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", {}) if response.content else {}
        except (ValueError, json.JSONDecodeError):
            detail = f"Server error ({response.status_code})"
        st.error(detail.get("message", detail) if isinstance(detail, dict) else detail)
        return None
    try:
        return response.json() if response.content else {}
    except (ValueError, json.JSONDecodeError):
        st.error("The server returned an unexpected response.")
        return None


def login_view():
    st.title("Healthcare AI Platform")
    st.caption("Multi-tenant patient access, scheduling, intake and AI operations")
    sign_in_tab, sign_up_tab = st.tabs(["Sign in", "Register"])
    with sign_in_tab:
        presets = {
            "Patient": ("patient@example.com", "DemoPatient!2026"),
            "Doctor": ("doctor@example.com", "DemoDoctor!2026"),
            "Hospital Admin": ("hospital.admin@example.com", "DemoHospital!2026"),
            "Platform Admin": ("platform.admin@example.com", "DemoAdmin!2026"),
            "Custom": ("", ""),
        }
        choice = st.selectbox("Demo account", presets)
        default_email, default_password = presets[choice]
        with st.form("login"):
            email = st.text_input("Email", value=default_email)
            password = st.text_input("Password", value=default_password, type="password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            result = api("POST", "/auth/login", data={"email": email, "password": password})
            if result:
                st.session_state.token = result["access_token"]
                st.session_state.me = api("GET", "/auth/me")
                st.rerun()
    with sign_up_tab:
        st.caption("Patients, doctors, and hospitals can all self-register here. Platform accounts are provisioned separately.")
        account_type = st.radio("I am a", ["Patient", "Doctor", "Hospital"], horizontal=True, key="reg_account_type")
        if account_type == "Doctor":
            st.info(
                "After you create your account, a hospital administrator still needs to invite "
                "you (by this email) into their hospital before you can see appointments or set "
                "your availability. Until then your dashboard will show a pending-invite notice."
            )
        elif account_type == "Hospital":
            st.info(
                "This creates your hospital administrator account. After signing in you'll fill in "
                "your hospital's details and submit them for Platform Admin approval — only approved "
                "hospitals can add doctors and receive bookings."
            )
        with st.form("register"):
            full_name = st.text_input(
                "Full name" if account_type != "Hospital" else "Your name (hospital administrator)",
            )
            reg_email = st.text_input("Email", key="reg_email")
            reg_password = st.text_input(
                "Password", type="password", key="reg_password",
                help="At least 10 characters.",
            )
            confirm_password = st.text_input("Confirm password", type="password")
            reg_submitted = st.form_submit_button("Create account", type="primary")
        if reg_submitted:
            if not full_name or not reg_email or not reg_password:
                st.error("Please fill in your name, email, and password.")
            elif reg_password != confirm_password:
                st.error("Passwords do not match.")
            elif len(reg_password) < 10:
                st.error("Password must be at least 10 characters.")
            else:
                role = {"Patient": "PATIENT", "Doctor": "DOCTOR", "Hospital": "HOSPITAL_ADMIN"}[account_type]
                created = api("POST", "/auth/register", data={
                    "email": reg_email, "password": reg_password,
                    "full_name": full_name, "role": role,
                })
                if created:
                    result = api("POST", "/auth/login", data={"email": reg_email, "password": reg_password})
                    if result:
                        st.session_state.token = result["access_token"]
                        st.session_state.me = api("GET", "/auth/me")
                        st.rerun()


def voice_component():
    result = _voice_component(
        token=st.session_state.token,
        api_base=BROWSER_API_BASE,
        conversation_id=st.session_state.get("conversation_id"),
        key="voice_assistant",
    )
    if result and result.get("conversation_id") and result["conversation_id"] != st.session_state.get("conversation_id"):
        st.session_state.conversation_id = result["conversation_id"]


def patient_dashboard():
    assistant_tab, discover_tab, appointments_tab, questionnaire_tab, profile_tab = st.tabs(
        ["AI Assistant", "Find & Book", "Appointments", "Questionnaires", "Profile"]
    )
    with assistant_tab:
        voice_component()
        st.subheader("Text fallback")
        if "messages" not in st.session_state:
            st.session_state.messages = []
        for role, text in st.session_state.messages:
            with st.chat_message(role): st.write(text)
        if prompt := st.chat_input("Describe the care or appointment task you need"):
            st.session_state.messages.append(("user", prompt))
            payload = {"message": prompt, "conversation_id": st.session_state.get("conversation_id")}
            result = api("POST", "/agent/messages", data=payload)
            if result:
                st.session_state.conversation_id = result.get("conversation_id")
                st.session_state.messages.append(("assistant", result["text"]))
                st.rerun()
    with discover_tab:
        st.subheader("1. Find a hospital")
        hospital_query = st.text_input("Search hospitals by name", key="discover_hospital_query")
        hospitals = api("GET", "/hospitals") or []
        if hospital_query:
            hospitals = [h for h in hospitals if hospital_query.casefold() in h["name"].casefold()]
        hospital_options = {f"{h['name']} — {h.get('address') or 'address on file'}": h["id"] for h in hospitals}
        if not hospital_options:
            st.info("No approved hospitals match yet. Try clearing the search.")
        else:
            selected_hospital_label = st.selectbox("Hospital", list(hospital_options), key="discover_hospital_select")
            selected_hospital_id = hospital_options[selected_hospital_label]

            st.subheader("2. Find a doctor")
            specialty_filter = st.text_input("Filter by specialty (optional)", key="discover_specialty")
            doctors = api("GET", "/doctors", params={
                "hospital_id": selected_hospital_id,
                **({"specialty": specialty_filter} if specialty_filter else {}),
            }) or []
            doctor_options = {f"{d['name']} — {d['specialty']}": d for d in doctors}
            if not doctor_options:
                st.info("No active doctors match that filter at this hospital.")
            else:
                selected_doctor_label = st.selectbox("Doctor", list(doctor_options), key="discover_doctor_select")
                selected_doctor = doctor_options[selected_doctor_label]

                st.subheader("3. Check real availability")
                appointment_types = api("GET", "/appointment-types", params={
                    "hospital_id": selected_hospital_id,
                    "specialty_id": selected_doctor["specialty_id"],
                }) or []
                if not appointment_types:
                    st.info("This hospital has no active appointment type configured for this specialty.")
                else:
                    type_options = {t["name"]: t for t in appointment_types}
                    selected_type_label = st.selectbox("Appointment type", list(type_options), key="discover_type_select")
                    selected_type = type_options[selected_type_label]
                    date_from = st.date_input("From date", value=date.today(), key="discover_date_from")
                    date_to = st.date_input("To date", value=date.today() + timedelta(days=7), key="discover_date_to")
                    if st.button("Check availability", key="discover_check_availability"):
                        slots = api("GET", "/availability", params={
                            "doctor_id": selected_doctor["id"],
                            "appointment_type_id": selected_type["id"],
                            "date_from": date_from.isoformat(),
                            "date_to": date_to.isoformat(),
                        }) or []
                        st.session_state.discover_slots = slots
                        st.session_state.discover_type_id = selected_type["id"]
                        st.session_state.discover_doctor_id = selected_doctor["id"]

                    slots = st.session_state.get("discover_slots") or []
                    if slots and st.session_state.get("discover_doctor_id") == selected_doctor["id"]:
                        st.subheader("4. Book a real slot")
                        slot_options = {s["starts_at"]: s for s in slots}
                        chosen_slot = st.selectbox("Available slot", list(slot_options), key="discover_slot_select")
                        st.caption("This slot quote is held for a few minutes. Booking confirms it exactly as shown.")
                        if st.button("Book this slot", type="primary", key="discover_book_button"):
                            key = f"ui-book-{selected_doctor['id']}-{chosen_slot}"
                            quote_id = slot_options[chosen_slot]["slot_quote_id"]
                            booked = api("POST", "/appointments", data={
                                "doctor_id": selected_doctor["id"],
                                "appointment_type_id": st.session_state.discover_type_id,
                                "starts_at": chosen_slot,
                                "idempotency_key": key,
                                "slot_quote_id": quote_id,
                                "confirmation_token": quote_id,
                            })
                            if booked:
                                status_label = "confirmed" if booked["status"] == "CONFIRMED" else booked["status"].lower()
                                st.success(f"Appointment #{booked['id']} is {status_label}.")
                                st.session_state.discover_slots = []
                    elif st.session_state.get("discover_doctor_id") == selected_doctor["id"]:
                        st.info("No real available slots in that date range. Try a different range.")
    with appointments_tab:
        items = api("GET", "/appointments") or []
        if not items: st.info("No appointments yet.")
        for item in items:
            with st.expander(f"Appointment #{item['id']} · {item['status']} · {item['starts_at']}"):
                st.json(item)
                if item["status"] in {"CONFIRMED", "RESCHEDULED"}:
                    col_cancel, col_reschedule = st.columns(2)
                    with col_cancel:
                        if st.button("Cancel", key=f"cancel-{item['id']}"):
                            api("POST", f"/appointments/{item['id']}/cancel", data={"idempotency_key": f"ui-cancel-{item['id']}-{item['version']}", "expected_version": item["version"], "reason": "Patient requested"})
                            st.rerun()
                    with col_reschedule:
                        reschedule_key = f"reschedule-open-{item['id']}"
                        if st.button("Reschedule", key=f"reschedule-btn-{item['id']}"):
                            st.session_state[reschedule_key] = True
                    if st.session_state.get(reschedule_key):
                        new_date_from = st.date_input("From date", value=date.today(), key=f"reschedule-from-{item['id']}")
                        new_date_to = st.date_input("To date", value=date.today() + timedelta(days=7), key=f"reschedule-to-{item['id']}")
                        if st.button("Check new slots", key=f"reschedule-check-{item['id']}"):
                            new_slots = api("GET", "/availability", params={
                                "doctor_id": item["doctor_id"],
                                "appointment_type_id": item["appointment_type_id"],
                                "date_from": new_date_from.isoformat(),
                                "date_to": new_date_to.isoformat(),
                            }) or []
                            st.session_state[f"reschedule-slots-{item['id']}"] = new_slots
                        new_slots = st.session_state.get(f"reschedule-slots-{item['id']}") or []
                        if new_slots:
                            slot_map = {s["starts_at"]: s for s in new_slots}
                            picked = st.selectbox("New slot", list(slot_map), key=f"reschedule-select-{item['id']}")
                            if st.button("Confirm reschedule", type="primary", key=f"reschedule-confirm-{item['id']}"):
                                quote_id = slot_map[picked]["slot_quote_id"]
                                result = api("POST", f"/appointments/{item['id']}/reschedule", data={
                                    "starts_at": picked,
                                    "idempotency_key": f"ui-reschedule-{item['id']}-{item['version']}-{picked}",
                                    "expected_version": item["version"],
                                    "slot_quote_id": quote_id,
                                    "confirmation_token": quote_id,
                                })
                                if result:
                                    st.success(f"Appointment #{result['id']} rescheduled to {result['starts_at']}.")
                                    st.session_state[reschedule_key] = False
                                    st.session_state[f"reschedule-slots-{item['id']}"] = []
                                    st.rerun()
    with questionnaire_tab:
        assignments = api("GET", "/questionnaire-assignments") or []
        if not assignments: st.info("No questionnaires are currently assigned.")
        for assignment in assignments:
            detail = api("GET", f"/questionnaire-assignments/{assignment['id']}")
            if not detail: continue
            with st.form(f"questionnaire-{assignment['id']}"):
                st.subheader(f"Questionnaire #{assignment['questionnaire_id']}")
                answers = {}
                for q in detail["questions"]:
                    label = q["prompt"] + (" *" if q["required"] else "")
                    field_key = f"q-{assignment['id']}-{q['id']}"
                    if q["question_type"] == "YES_NO": answers[q["id"]] = st.radio(label, [True, False], format_func=lambda x: "Yes" if x else "No", key=field_key)
                    elif q["question_type"] == "CHOICE": answers[q["id"]] = st.selectbox(label, q["options"], key=field_key)
                    elif q["question_type"] == "MULTIPLE_CHOICE": answers[q["id"]] = st.multiselect(label, q["options"], key=field_key)
                    elif q["question_type"] == "NUMERIC": answers[q["id"]] = st.number_input(label, key=field_key)
                    elif q["question_type"] == "DATE": answers[q["id"]] = st.date_input(label, key=field_key).isoformat()
                    else: answers[q["id"]] = st.text_area(label, key=field_key)
                if st.form_submit_button("Submit responses"):
                    if api("POST", f"/questionnaire-assignments/{assignment['id']}/answers", data={"answers": answers}): st.success("Responses submitted.")
    with profile_tab:
        st.write(st.session_state.me)
        patient = api("GET", "/patients/me") or {}
        st.subheader("Profile")
        with st.form("profile"):
            name = st.text_input("Name", value=patient.get("name") or "")
            phone = st.text_input("Phone", value=patient.get("phone") or "")
            dob = st.date_input(
                "Date of birth",
                value=date.fromisoformat(patient["date_of_birth"]) if patient.get("date_of_birth") else None,
            )
            comm_pref = st.selectbox(
                "Communication preference", ["EMAIL", "SMS", "VOICE"],
                index=["EMAIL", "SMS", "VOICE"].index(patient.get("communication_preference") or "EMAIL"),
            )
            if st.form_submit_button("Save profile"):
                if api("PATCH", "/patients/me", data={
                    "name": name, "phone": phone or None,
                    "date_of_birth": dob.isoformat() if dob else None,
                    "communication_preference": comm_pref,
                }):
                    st.success("Profile updated.")
                    st.rerun()

        st.subheader("Preferences")
        saved_preferences = patient.get("preferences") or {}
        if saved_preferences:
            st.table([{"preference": k, "value": v} for k, v in saved_preferences.items()])
        else:
            st.info("No preferences saved yet.")
        with st.form("preference"):
            key = st.text_input("Preference name", value="preferred_time")
            value = st.text_input("Preference value", value="morning")
            if st.form_submit_button("Save preference"):
                if api("PUT", "/patients/me/preferences", data={"key": key, "value": value}):
                    st.success("Saved")
                    st.rerun()


def hospital_dashboard():
    hospitals = api("GET", "/hospitals") or []

    if not hospitals:
        st.info("You don't have a hospital yet. Register one below to get started.")
        with st.form("create-hospital"):
            name = st.text_input("Hospital / organization name")
            address = st.text_area("Address")
            contact_email = st.text_input("Contact email")
            contact_phone = st.text_input("Contact phone (optional)")
            supported_systems = st.text_input(
                "Supported healthcare systems (comma separated, optional)",
                help="E.g. Mock EHR. This selects which integration connectors you can configure later.",
            )
            submitted = st.form_submit_button("Register hospital", type="primary")
        if submitted:
            if not name or not address or not contact_email:
                st.error("Hospital name, address, and contact email are required.")
            else:
                created = api("POST", "/hospitals", data={
                    "name": name,
                    "address": address,
                    "contact_email": contact_email,
                    "contact_phone": contact_phone or None,
                    "supported_systems": [x.strip() for x in supported_systems.split(",") if x.strip()],
                })
                if created:
                    st.success(f"{created['name']} was created in DRAFT status. Finish configuring it, then submit it for Platform Admin review.")
                    st.rerun()
        return

    tabs = st.tabs(["Overview", "Configuration", "Operations"])
    with tabs[0]:
        st.dataframe(hospitals, use_container_width=True)
        current = hospitals[0]
        if current["status"] == "CORRECTIONS_REQUIRED" and current.get("review_notes"):
            st.warning(f"Platform Admin requested corrections: {current['review_notes']}")
        elif current["status"] == "REJECTED" and current.get("review_notes"):
            st.error(f"This hospital application was rejected: {current['review_notes']}")
        if current["status"] in {"DRAFT", "CORRECTIONS_REQUIRED", "REJECTED"}:
            if st.button("Submit hospital for review"):
                if api("POST", f"/hospitals/{current['id']}/submit"):
                    st.success("Submitted for Platform Admin review.")
                    st.rerun()
        elif current["status"] in {"SUBMITTED", "UNDER_REVIEW"}:
            st.info("Your application is awaiting Platform Admin review.")
        elif current["status"] == "APPROVED":
            st.success("This hospital is approved and can accept bookings.")
        elif current["status"] == "SUSPENDED":
            st.error("This hospital is currently suspended by the Platform Admin.")
    hospital_id = hospitals[0]["id"]
    with tabs[1]:
        config = api("GET", f"/hospitals/{hospital_id}/configuration") or {}
        for key in ["departments", "specialties", "doctors", "appointment_types", "calendars", "questionnaires", "workflows", "connections"]:
            with st.expander(key.replace("_", " ").title()): st.dataframe(config.get(key, []), use_container_width=True)
        with st.form("department"):
            name = st.text_input("New department")
            if st.form_submit_button("Create department") and name:
                api("POST", f"/hospitals/{hospital_id}/departments", data={"name": name}); st.rerun()

        with st.form("specialty"):
            specialty_name = st.text_input("New specialty")
            specialty_department_options = {"None": None, **{d["name"]: d["id"] for d in config.get("departments", [])}}
            specialty_department_label = st.selectbox("Department (optional)", list(specialty_department_options), key="new_specialty_department")
            if st.form_submit_button("Create specialty") and specialty_name:
                if api("POST", f"/hospitals/{hospital_id}/specialties", data={
                    "name": specialty_name,
                    "department_id": specialty_department_options[specialty_department_label],
                }):
                    st.rerun()

        specialties = config.get("specialties", [])
        st.subheader("Invite a doctor")
        st.caption(
            "The doctor must first self-register (Register tab, role: Doctor). Once invited here, "
            "their profile is PENDING_APPROVAL until the Platform Admin approves it — only then can "
            "patients find or book them."
        )
        unlinked_accounts = api("GET", "/doctors/unlinked-accounts") or []
        if not specialties:
            st.info("Create a specialty first so you can assign the doctor to it.")
        elif not unlinked_accounts:
            st.info("No registered doctor accounts are waiting to be linked. Ask the doctor to register first.")
        else:
            account_options = {f"{a['full_name']} ({a['email']})": a for a in unlinked_accounts}
            account_label = st.selectbox("Registered doctor account", list(account_options), key="invite_account_select")
            selected_account = account_options[account_label]
            with st.form("invite-doctor"):
                doctor_name = st.text_input("Display name", value=selected_account["full_name"])
                specialty_options = {s["name"]: s["id"] for s in specialties}
                specialty_label = st.selectbox("Specialty", list(specialty_options))
                department_options = {"None": None, **{d["name"]: d["id"] for d in config.get("departments", [])}}
                department_label = st.selectbox("Department", list(department_options))
                qualifications = st.text_input("Qualifications (optional)")
                experience_years = st.number_input("Years of experience (optional)", min_value=0, max_value=80, value=0)
                languages = st.text_input("Languages (comma separated, optional)")
                if st.form_submit_button("Send invite", type="primary"):
                    invited = api("POST", f"/hospitals/{hospital_id}/doctors", data={
                        "name": doctor_name,
                        "specialty_id": specialty_options[specialty_label],
                        "department_id": department_options[department_label],
                        "user_email": selected_account["email"],
                        "qualifications": qualifications or None,
                        "experience_years": int(experience_years) or None,
                        "languages": [x.strip() for x in languages.split(",") if x.strip()],
                    })
                    if invited:
                        st.success(f"{invited['name']} is invited and awaiting Platform Admin approval.")
                        st.rerun()

        all_doctors = config.get("doctors", [])
        active_doctors = [d for d in all_doctors if d["status"] == "ACTIVE"]
        appointment_types = config.get("appointment_types", [])
        existing_calendars = config.get("calendars", [])
        st.subheader("Create a calendar for a doctor")
        st.caption(
            "A calendar links one doctor to one appointment type and its own timezone — it's the "
            "unit patients book against. After creating it here, the doctor still needs to add "
            "working hours in their own dashboard (Availability & Calendar tab) before any slot "
            "becomes bookable."
        )
        pending_count = len([d for d in all_doctors if d["status"] == "PENDING_APPROVAL"])
        if pending_count:
            st.info(f"{pending_count} invited doctor(s) are still awaiting Platform Admin approval and won't appear below yet.")
        if not active_doctors or not appointment_types:
            st.info("You need at least one approved (ACTIVE) doctor and one appointment type before creating a calendar.")
        else:
            with st.form("create-calendar"):
                doctor_options = {d["name"]: d["id"] for d in active_doctors}
                doctor_label = st.selectbox("Doctor", list(doctor_options), key="calendar_doctor_select")
                type_options = {t["name"]: t["id"] for t in appointment_types}
                type_label = st.selectbox("Appointment type", list(type_options), key="calendar_type_select")
                timezone_value = st.text_input("Timezone", value="Asia/Kolkata", help="IANA timezone name, e.g. Asia/Kolkata for IST.")
                calendar_submitted = st.form_submit_button("Create calendar")
            if calendar_submitted:
                duplicate = any(
                    c["doctor_id"] == doctor_options[doctor_label] and c["appointment_type_id"] == type_options[type_label]
                    for c in existing_calendars
                )
                if duplicate:
                    st.error(f"{doctor_label} already has a calendar for {type_label}.")
                else:
                    created_calendar = api("POST", f"/hospitals/{hospital_id}/calendars", data={
                        "doctor_id": doctor_options[doctor_label],
                        "appointment_type_id": type_options[type_label],
                        "timezone": timezone_value,
                    })
                    if created_calendar:
                        st.success(
                            f"Calendar #{created_calendar['id']} created for {doctor_label}. "
                            f"Ask them to add working hours in their dashboard to start receiving bookings."
                        )
                        st.rerun()
    with tabs[2]:
        st.subheader("Operational metrics"); st.json(api("GET", "/operations/metrics") or {})
        st.subheader("Reconciliation"); st.dataframe(api("GET", "/operations/reconciliation") or [], use_container_width=True)
        st.subheader("Audit"); st.dataframe(api("GET", "/operations/audit") or [], use_container_width=True)


WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def doctor_dashboard():
    profile = api("GET", "/doctors/me")
    if profile is None:
        st.warning(
            "Your account is registered but not yet linked to a hospital. "
            "Ask your hospital administrator to invite you using this account's email. "
            "Nothing else in this dashboard will work until then."
        )
        return

    appointments_tab, calendar_tab, previsit_tab, profile_tab = st.tabs(
        ["Appointments", "Availability & Calendar", "Pre-visit responses", "Profile"]
    )

    with appointments_tab:
        items = api("GET", "/appointments") or []
        if not items:
            st.info("No appointments yet.")
        for item in items:
            with st.expander(f"Appointment #{item['id']} · {item['status']} · {to_ist(item['starts_at'])}"):
                display = {**item, "starts_at": to_ist(item["starts_at"]), "ends_at": to_ist(item.get("ends_at"))}
                st.json(display)

    with calendar_tab:
        calendars = api("GET", "/calendars") or []
        if not calendars:
            st.info("No calendar has been created for you yet. Ask your hospital administrator to create one.")
        else:
            calendar_options = {f"Calendar #{c['id']} ({c['timezone']})": c for c in calendars}
            calendar_label = st.selectbox("Calendar", list(calendar_options), key="doctor_calendar_select")
            calendar = calendar_options[calendar_label]

            st.subheader("Working hours")
            st.caption(f"Times are this calendar's local clock time ({calendar['timezone']}), 24-hour format.")
            hours = sorted(api("GET", f"/calendars/{calendar['id']}/working-hours") or [], key=lambda h: (h["weekday"], h["start_time"]))
            if hours:
                st.table([
                    {"day": WEEKDAY_NAMES[h["weekday"]], "start": time_to_ist_label(h["start_time"]), "end": time_to_ist_label(h["end_time"])}
                    for h in hours
                ])
            else:
                st.info("No working hours set yet — patients cannot book you until you add some.")
            with st.form("add-working-hours"):
                weekday_label = st.selectbox("Day", WEEKDAY_NAMES)
                start_time = st.time_input("Start time", value=time(9, 0), step=timedelta(minutes=15))
                end_time = st.time_input("End time", value=time(17, 0), step=timedelta(minutes=15))
                add_hours_submitted = st.form_submit_button("Add working hours")
            if add_hours_submitted:
                selected_weekday = WEEKDAY_NAMES.index(weekday_label)
                if start_time >= end_time:
                    st.error("Start time must be before end time.")
                elif any(
                    h["weekday"] == selected_weekday
                    and start_time.isoformat() < h["end_time"] and end_time.isoformat() > h["start_time"]
                    for h in hours
                ):
                    st.error(f"That overlaps with existing working hours already set for {weekday_label}.")
                else:
                    result = api("POST", f"/calendars/{calendar['id']}/working-hours", data={
                        "weekday": selected_weekday,
                        "start_time": start_time.isoformat(),
                        "end_time": end_time.isoformat(),
                    })
                    if result:
                        st.success("Working hours added.")
                        st.rerun()

            st.subheader("Blocked periods / leave")
            st.caption("Enter times in IST (24-hour). They are stored and matched in UTC internally.")
            blocks = api("GET", f"/calendars/{calendar['id']}/blocked-periods") or []
            if blocks:
                st.table([
                    {"kind": b["kind"], "starts_at": to_ist(b["starts_at"]), "ends_at": to_ist(b["ends_at"]), "reason": b.get("reason") or ""}
                    for b in sorted(blocks, key=lambda b: b["starts_at"])
                ])
            else:
                st.info("No blocked periods or leave recorded.")
            with st.form("add-blocked-period"):
                block_kind = st.selectbox("Type", ["BLOCK", "LEAVE"])
                block_start_date = st.date_input("Start date (IST)", value=date.today())
                block_start_time = st.time_input("Start time (IST)", value=time(9, 0), step=timedelta(minutes=15))
                block_end_date = st.date_input("End date (IST)", value=date.today())
                block_end_time = st.time_input("End time (IST)", value=time(17, 0), step=timedelta(minutes=15))
                block_reason = st.text_input("Reason (optional)")
                add_block_submitted = st.form_submit_button("Add block / leave")
            if add_block_submitted:
                starts_at_ist = datetime.combine(block_start_date, block_start_time, tzinfo=IST)
                ends_at_ist = datetime.combine(block_end_date, block_end_time, tzinfo=IST)
                if starts_at_ist >= ends_at_ist:
                    st.error("Start must be before end.")
                else:
                    result = api("POST", f"/calendars/{calendar['id']}/blocked-periods", data={
                        "starts_at": starts_at_ist.astimezone(timezone.utc).isoformat(),
                        "ends_at": ends_at_ist.astimezone(timezone.utc).isoformat(),
                        "reason": block_reason or None,
                        "kind": block_kind,
                    })
                    if result:
                        st.success("Blocked period added.")
                        st.rerun()

    with previsit_tab:
        assignments = api("GET", "/questionnaire-assignments") or []
        if not assignments:
            st.info("No pre-visit questionnaires are assigned for your patients yet.")
        for assignment in assignments:
            with st.expander(f"Assignment #{assignment['id']} · questionnaire #{assignment['questionnaire_id']} · {assignment['status']}"):
                if assignment["status"] != "COMPLETED":
                    st.caption("Not yet completed by the patient.")
                else:
                    if st.button("Load authorized responses", key=f"load-responses-{assignment['id']}"):
                        st.session_state[f"responses-{assignment['id']}"] = api(
                            "GET", f"/questionnaire-assignments/{assignment['id']}/responses"
                        )
                    responses = st.session_state.get(f"responses-{assignment['id']}")
                    if responses:
                        st.json(responses)

    with profile_tab:
        st.json(profile)


def platform_dashboard():
    overview_tab, hospital_tab, doctor_tab, health_tab, audit_tab = st.tabs(
        ["Overview", "Hospital review", "Doctor review", "Platform health", "Audit"]
    )
    hospitals = api("GET", "/hospitals") or []
    doctors = api("GET", "/admin/doctors") or []
    patients = api("GET", "/admin/patients") or []

    with overview_tab:
        st.caption("Everything on the platform, in one place.")
        col1, col2, col3 = st.columns(3)
        col1.metric("Hospitals", len(hospitals))
        col2.metric("Doctors", len(doctors))
        col3.metric("Patients", len(patients))

        pending_hospitals = [h for h in hospitals if h["status"] in {"SUBMITTED", "UNDER_REVIEW", "CORRECTIONS_REQUIRED"}]
        pending_doctors = [d for d in doctors if d["status"] == "PENDING_APPROVAL"]
        if pending_hospitals or pending_doctors:
            st.warning(f"{len(pending_hospitals)} hospital application(s) and {len(pending_doctors)} doctor profile(s) awaiting your approval.")

        st.subheader("Hospitals")
        st.dataframe(hospitals, use_container_width=True)
        st.subheader("Doctors")
        st.dataframe(doctors, use_container_width=True)
        st.subheader("Patients")
        st.dataframe(patients, use_container_width=True)

    with hospital_tab:
        st.dataframe(hospitals, use_container_width=True)
        reviewable = [h for h in hospitals if h["status"] in {"SUBMITTED", "UNDER_REVIEW", "CORRECTIONS_REQUIRED"}]
        if not reviewable:
            st.info("No hospital applications are awaiting review.")
        else:
            selected = st.selectbox("Application", reviewable, format_func=lambda x: f"#{x['id']} {x['name']}")
            decision = st.selectbox("Decision", ["APPROVED", "CORRECTIONS_REQUIRED", "REJECTED"])
            notes = st.text_area("Review notes", key="hospital_review_notes")
            if st.button("Submit hospital review"):
                if api("POST", f"/admin/hospitals/{selected['id']}/review", data={"decision": decision, "notes": notes}):
                    st.success(f"Hospital #{selected['id']} marked {decision}.")
                    st.rerun()

        st.subheader("Suspend / reactivate an approved hospital")
        active_hospitals = [h for h in hospitals if h["status"] in {"APPROVED", "SUSPENDED"}]
        if active_hospitals:
            target = st.selectbox("Hospital", active_hospitals, format_func=lambda x: f"#{x['id']} {x['name']} ({x['status']})", key="hospital_status_target")
            action = "reactivate" if target["status"] == "SUSPENDED" else "suspend"
            if st.button(f"{action.title()} hospital"):
                if api("POST", f"/admin/hospitals/{target['id']}/{action}"):
                    st.success(f"Hospital #{target['id']} {action}d.")
                    st.rerun()

    with doctor_tab:
        st.dataframe(doctors, use_container_width=True)
        pending = [d for d in doctors if d["status"] == "PENDING_APPROVAL"]
        st.subheader("Pending doctor profiles")
        if not pending:
            st.info("No doctor profiles are awaiting approval.")
        else:
            selected_doctor = st.selectbox("Doctor profile", pending, format_func=lambda x: f"#{x['id']} {x['name']} (hospital #{x['hospital_id']})")
            doctor_decision = st.selectbox("Decision", ["APPROVED", "REJECTED"], key="doctor_decision")
            doctor_notes = st.text_area("Review notes", key="doctor_review_notes")
            if st.button("Submit doctor review"):
                if api("POST", f"/admin/doctors/{selected_doctor['id']}/review", data={"decision": doctor_decision, "notes": doctor_notes}):
                    st.success(f"Doctor #{selected_doctor['id']} marked {doctor_decision}.")
                    st.rerun()

        st.subheader("Suspend / reactivate an approved doctor")
        active_doctors = [d for d in doctors if d["status"] in {"ACTIVE", "SUSPENDED"}]
        if active_doctors:
            doc_target = st.selectbox("Doctor", active_doctors, format_func=lambda x: f"#{x['id']} {x['name']} ({x['status']})", key="doctor_status_target")
            doc_action = "reactivate" if doc_target["status"] == "SUSPENDED" else "suspend"
            if st.button(f"{doc_action.title()} doctor"):
                if api("POST", f"/admin/doctors/{doc_target['id']}/{doc_action}"):
                    st.success(f"Doctor #{doc_target['id']} {doc_action}d.")
                    st.rerun()

    with health_tab:
        st.json(api("GET", "/operations/metrics") or {})
    with audit_tab:
        st.dataframe(api("GET", "/operations/audit") or [], use_container_width=True)


if "token" not in st.session_state:
    login_view()
else:
    me = st.session_state.get("me") or api("GET", "/auth/me")
    st.session_state.me = me
    st.sidebar.title(me["role"].replace("_", " ").title())
    st.sidebar.write(me["full_name"])
    if st.sidebar.button("Sign out"):
        st.session_state.clear(); st.rerun()
    st.title("Healthcare AI Platform")
    if me["role"] == "PATIENT": patient_dashboard()
    elif me["role"] == "HOSPITAL_ADMIN": hospital_dashboard()
    elif me["role"] == "DOCTOR": doctor_dashboard()
    else: platform_dashboard()
