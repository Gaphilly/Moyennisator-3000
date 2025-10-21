#!/usr/bin/env python3
"""
Pronote Grade Analyzer - Web Application
A secure web interface for analyzing French academic grades from Pronote 
with Brevet statistics calculation.
"""

import os
import sys
import logging
from datetime import date, datetime
from io import BytesIO
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from uuid import uuid4

from flask import Flask, render_template, request, flash, redirect, url_for, jsonify, session, send_file
from urllib.parse import urlparse
from flask_babel import Babel, gettext
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length
from weasyprint import HTML

try:
    from pronotepy import Client
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Error: Missing required dependencies. Please install: {e}")
    print("Run: pip install pronotepy python-dotenv flask flask-wtf flask-babel wtforms")
    exit(1)

import json
from pathlib import Path

# ---------- Config & Logging ----------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("pronote_web.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Load secret key from environment; fallback to a random key only for dev
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
# Enable CSRF protection
app.config["WTF_CSRF_ENABLED"] = True
app.config["WTF_CSRF_SECRET_KEY"] = os.environ.get("SECRET_KEY") or os.urandom(32)
csrf = CSRFProtect()
csrf.init_app(app)

# ---------- I18N ----------

app.config["LANGUAGES"] = {"fr": "Français", "en": "English", "es": "Español"}
app.config["BABEL_DEFAULT_LOCALE"] = "fr"
app.config["BABEL_DEFAULT_TIMEZONE"] = "UTC"

def get_locale():
    # 1) URL param
    if "language" in request.args:
        language = request.args["language"]
        if language in app.config["LANGUAGES"]:
            session["language"] = language
            return language
    # 2) Session
    if "language" in session and session["language"] in app.config["LANGUAGES"]:
        return session["language"]
    # 3) Browser
    return request.accept_languages.best_match(app.config["LANGUAGES"].keys()) or app.config["BABEL_DEFAULT_LOCALE"]

babel = Babel()
babel.init_app(app, default_locale="fr", locale_selector=get_locale)

# Make available in templates
app.jinja_env.globals["get_locale"] = get_locale
app.jinja_env.globals["_"] = gettext
# expose useful objects to templates (used by base.html)
app.jinja_env.globals["app"] = app
app.jinja_env.globals["datetime"] = datetime

# Load pronote URL presets from urls.json if present
URLS_FILE = Path(__file__).parent / "urls.json"
DEFAULT_URL_PRESETS = []
try:
    if URLS_FILE.exists():
        with open(URLS_FILE, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
            DEFAULT_URL_PRESETS = data.get('presets', []) if isinstance(data, dict) else []
            logger.info(f"Loaded {len(DEFAULT_URL_PRESETS)} Pronote URL presets from {URLS_FILE}")
except Exception as e:
    logger.warning(f"Could not load URL presets: {e}")

# Make presets available in templates
app.jinja_env.globals['url_presets'] = DEFAULT_URL_PRESETS

# ---------- Forms ----------

class LoginForm(FlaskForm):
    """Form for Pronote login credentials."""
    def __init__(self, *args, **kwargs):
        super(LoginForm, self).__init__(*args, **kwargs)
        self.username.label.text = gettext("Username")
        self.password.label.text = gettext("Password")
        self.submit.label.text = gettext("Analyze Grades")
        self.username.render_kw = {"placeholder": gettext("Enter your Pronote username")}
        self.password.render_kw = {"placeholder": gettext("Enter your Pronote password")}

    username = StringField("Username", validators=[DataRequired(), Length(min=1, max=50)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=1, max=100)])
    submit = SubmitField("Analyze Grades")

# ---------- Per-session data store ----------
# For a small single-worker app this is fine.
# If you scale to multiple workers/dynos, move this to Redis.
STORE: Dict[str, Dict] = {}

# ---------- Analyzer (stateless per request) ----------

class PronoteAnalyzer:
    """Main class for analyzing Pronote academic data (stateless)."""

    def __init__(self):
        self.fixed_url = "https://4170004n.index-education.net/pronote/eleve.html"

    def connect_and_fetch(self, username: str, password: str) -> Tuple[bool, str, List[Dict], Dict]:
        """
        Connect, fetch evaluations, then dispose of the client.
        Returns (success, message, evaluations, info_dict)
        """
        client: Optional[Client] = None
        try:
            logger.info("Attempting to connect to Pronote as {username} with password {password}...")
            # Use the fixed_url attribute unless overridden in this call via thread-local/session
            pronote_url = getattr(self, 'fixed_url', None) or self.fixed_url
            client = Client(pronote_url, username, password)

            if not getattr(client, "logged_in", False):
                logger.error("Login failed. Please check your credentials.")
                return False, gettext("Login failed. Please check your credentials."), [], {}

            logger.info(f"Successfully connected to Pronote!")

            # Fetch evaluations immediately, don't store client anywhere
            evaluations: List[Dict] = []
            periods = getattr(client, "periods", [])
            if not periods:
                logger.warning("No periods found")
                # still return basic student/class info when available
                info = {}
                try:
                    info_obj = getattr(client, "info", None)
                    if info_obj:
                        info = {
                            "student_name": getattr(info_obj, "name", "-"),
                            "class_name": getattr(info_obj, "class_name", "-"),
                        }
                except Exception:
                    info = {}
                return True, gettext("No periods found"), [], info

            for period in periods:
                for evaluation in getattr(period, "evaluations", []):
                    try:
                        ev = self._process_evaluation(evaluation)
                        if ev:
                            evaluations.append(ev)
                    except Exception as e:
                        logger.warning(f"Error processing evaluation: {e}")
                        continue

            # gather student info from client if available
            info = {}
            try:
                info_obj = getattr(client, "info", None)
                if info_obj:
                    info = {
                        "student_name": getattr(info_obj, "name", "-"),
                        "class_name": getattr(info_obj, "class_name", "-"),
                    }
            except Exception:
                info = {}

            logger.info(f"Successfully fetched {len(evaluations)} evaluations")
            return True, gettext("Successfully fetched %(n)d evaluations", n=len(evaluations)), evaluations, info

        except Exception as e:
            msg = str(e)

            # Map cryptic errors to friendly bilingual messages
            if "Decryption failed while trying to un pad" in msg:
                error_msg = gettext("Incorrect password. / Mot de passe incorrect.")
            elif "Unknown error from pronote: 23" in msg or " 23" in msg:
                error_msg = gettext("Incorrect username. / Nom d'utilisateur incorrect.")
            else:
                error_msg = gettext("Unexpected connection error / Erreur de connexion inconnue: %(m)s", m=msg)

            logger.error(f"Connection error mapped: {error_msg}")
            return False, error_msg, [], {}
        finally:
            # Best effort cleanup (pronotepy doesn't necessarily need explicit close)
            del client

    def grade_to_points(self, grade: str) -> int:
        mapping = {"A+": 50, "A": 40, "C": 25, "E": 10}
        return mapping.get(grade, 0)

    def convert_grade_for_display(self, grade: str) -> str:
        mapping = {"A+": "V+", "A": "V", "C": "J", "E": "R"}
        return mapping.get(grade, grade)

    def _process_evaluation(self, evaluation) -> Optional[Dict]:
        try:
            acquisitions = getattr(evaluation, "acquisitions", [])
            points: List[int] = []
            grades: List[str] = []

            for acq in acquisitions:
                raw = getattr(acq, "abbreviation", "")
                if raw:
                    grades.append(self.convert_grade_for_display(raw))
                    points.append(self.grade_to_points(raw))

            avg_points = sum(points) / len(points) if points else 0
            subject_obj = getattr(evaluation, "subject", None)
            subject_name = getattr(subject_obj, "name", "Unknown") if subject_obj else "Unknown"

            # Normalize/keep a datetime.date object for sorting, and a display string
            raw_date = getattr(evaluation, "date", None)
            date_obj = None
            if isinstance(raw_date, date):
                date_obj = raw_date
            elif isinstance(raw_date, datetime):
                date_obj = raw_date.date()
            else:
                # try to parse ISO-like strings, otherwise leave None
                try:
                    date_obj = datetime.fromisoformat(str(raw_date)).date()
                except Exception:
                    date_obj = None

            # Display dates as dd/mm/YYYY when we have a date object
            date_display = date_obj.strftime("%d/%m/%Y") if date_obj else str(raw_date or "Unknown")

            return {
                "subject": subject_name,
                "date": date_display,
                "date_obj": date_obj,
                "name": getattr(evaluation, "name", "Unnamed"),
                "coefficient": getattr(evaluation, "coefficient", 1),
                "grades": grades,
                "average_points": round(avg_points, 2),
            }
        except Exception as e:
            logger.warning(f"Error processing individual evaluation: {e}")
            return None

    def calculate_subject_averages(self, evaluations: List[Dict]) -> Dict[str, float]:
        if not evaluations:
            return {}
        totals = defaultdict(lambda: {"points": 0.0, "coeffs": 0.0})
        for ev in evaluations:
            coeff = float(ev.get("coefficient", 0) or 0)
            avg = float(ev.get("average_points", 0) or 0)
            subject = ev.get("subject", "Unknown")
            totals[subject]["points"] += avg * coeff
            totals[subject]["coeffs"] += coeff
        out: Dict[str, float] = {}
        # Convert subject averages from the internal 0-50 scale to 0-20 for display:
        # factor = 20 / 50 = 0.4 (same conversion used for overall moyenne_sur_20)
        for subject, v in totals.items():
            avg_50 = (v["points"] / v["coeffs"]) if v["coeffs"] > 0 else 0.0
            avg_20 = round(avg_50 * 0.4, 2)
            out[subject] = avg_20
        return out

    def compute_brevet_stats(self, evaluations: List[Dict]) -> Dict[str, float]:
        if not evaluations:
            return {"moyenne_points": 0, "moyenne_sur_20": 0, "socle_sur_400": 0}
        total_coeff = sum(float(ev.get("coefficient", 0) or 0) for ev in evaluations)
        if total_coeff == 0:
            return {"moyenne_points": 0, "moyenne_sur_20": 0, "socle_sur_400": 0}
        total_points = sum(float(ev.get("coefficient", 0) or 0) * float(ev.get("average_points", 0) or 0) for ev in evaluations)
        moyenne_points = total_points / total_coeff
        return {
            "moyenne_points": round(moyenne_points, 2),
            "moyenne_sur_20": round(moyenne_points * 0.4, 2),
            "socle_sur_400": round(moyenne_points * 8, 2),
        }

    def get_performance_level(self, socle_score: float) -> str:
        if socle_score >= 350:
            return gettext("Excellent (Mention Très Bien possible)")
        elif socle_score >= 280:
            return gettext("Good (Mention Bien possible)")
        elif socle_score >= 240:
            return gettext("Satisfactory (Mention Assez Bien possible)")
        elif socle_score >= 200:
            return gettext("Pass level")
        else:
            return gettext("Below pass level")

analyzer = PronoteAnalyzer()

# ---------- Routes ----------

@app.route("/", methods=["GET", "POST"])
def index():
    # Ensure session id
    if "sid" not in session:
        session["sid"] = str(uuid4())

    form = LoginForm()

    if request.method == "POST":
        logger.info(f"Form validation: {form.validate()}")
        logger.info(f"Form errors: {form.errors}")

        if form.validate_on_submit():
            username = str(form.username.data).strip()
            password = str(form.password.data)

            # Determine which URL to use: preset or custom
            selected_url = request.form.get('pronote_url_select')
            custom_url = request.form.get('pronote_url_custom', '').strip()
            logger.debug(f"User selected URL option: {selected_url}, custom provided: {'yes' if custom_url else 'no'}")
            # If user chose Other and provided a custom URL, use it
            if selected_url == 'other' and custom_url:
                chosen_url = custom_url
                logger.debug("Using custom Pronote URL provided by user")
            else:
                # Find matching preset URL by value
                chosen_url = None
                for p in DEFAULT_URL_PRESETS:
                    if p.get('url') == selected_url or p.get('name') == selected_url:
                        chosen_url = p.get('url')
                        break
                # Fallback to selected_url if it looks like a URL
                if not chosen_url:
                    chosen_url = selected_url
                logger.debug(f"Resolved chosen_url from presets or selection: {chosen_url}")

            # Validate chosen_url: allow query strings and trailing slashes by parsing path
            try:
                parsed = urlparse(str(chosen_url))
                path = str(parsed.path or '')
                normalized_path = path.rstrip('/')
                logger.debug(f"Parsed URL path '{path}' -> normalized '{normalized_path}'")
                if not normalized_path.endswith('/pronote') and not normalized_path.endswith('/eleve.html'):
                    logger.info(f"Rejected Pronote URL on validation: {chosen_url}")
                    flash(gettext("Please select or enter a valid Pronote URL that ends with /pronote or /eleve.html."), "error")
                    return render_template("index.html", form=form, languages=app.config['LANGUAGES'], url_presets=DEFAULT_URL_PRESETS)
            except Exception as e:
                logger.warning(f"Error parsing Pronote URL '{chosen_url}': {e}")
                flash(gettext("Please select or enter a valid Pronote URL that ends with /pronote or /eleve.html."), "error")
                return render_template("index.html", form=form, languages=app.config['LANGUAGES'], url_presets=DEFAULT_URL_PRESETS)

            # set analyzer.url for this run
            analyzer.fixed_url = str(chosen_url)
            logger.info(f"Set analyzer.fixed_url to {analyzer.fixed_url}")

            logger.info(f"Processing login for user: {username} against {analyzer.fixed_url}")

            success, message, evaluations, info = analyzer.connect_and_fetch(username, password)
            if not success:
                logger.info(f"Connection attempt failed for user {username}: {message}")
                flash(gettext("Connection failed: %(message)s", message=message), "error")
                return render_template("index.html", form=form)

            # Compute and stash per-session results in memory
            # sort evaluations newest first using the date_obj field (fallback to minimal date)
            evaluations_sorted = sorted(
                evaluations,
                key=lambda e: e.get("date_obj") or date.min,
                reverse=True
            )

            subject_averages = analyzer.calculate_subject_averages(evaluations_sorted)
            brevet_stats = analyzer.compute_brevet_stats(evaluations_sorted)
            performance_level = analyzer.get_performance_level(brevet_stats["socle_sur_400"])
            logger.info(f"Computed stats: {len(evaluations_sorted)} evaluations, moyenne_sur_20={brevet_stats.get('moyenne_sur_20')}")

            STORE[session["sid"]] = {
                "evaluations": evaluations_sorted,
                "subject_averages": subject_averages,
                "brevet_stats": brevet_stats,
                "performance_level": performance_level,
                "total_evaluations": len(evaluations_sorted),
                # Human-friendly export/report metadata
                "date": datetime.now().strftime("%d/%m/%Y"),
                "year": datetime.now().year,
                # student / class from Pronote client.info when available
                "student_name": (info.get("student_name") if isinstance(info, dict) else "-") if 'info' in locals() else "-",
                "class_name": (info.get("class_name") if isinstance(info, dict) else "-") if 'info' in locals() else "-",
            }

            flash(gettext("Success: %(message)s", message=message), "success")
            return redirect(url_for("results"))
        else:
            flash(gettext("Please check your input and try again."), "error")

    return render_template("index.html", form=form, languages=app.config['LANGUAGES'])

@app.route("/set_language/<language>")
def set_language(language=None):
    if language in app.config["LANGUAGES"]:
        session["language"] = language
    return redirect(request.referrer or url_for("index"))

@app.route("/results")
def results():
    sid = session.get("sid")
    if not sid or sid not in STORE:
        flash(gettext("No data available. Please login first."), "warning")
        return redirect(url_for("index"))

    data = STORE[sid]
    return render_template(
        "results.html",
        evaluations=data["evaluations"],
        subject_averages=data["subject_averages"],
        brevet_stats=data["brevet_stats"],
        performance_level=data["performance_level"],
        total_evaluations=data["total_evaluations"],
    )

@app.route("/api/data")
def api_data():
    sid = session.get("sid")
    if not sid or sid not in STORE:
        return jsonify({"error": "No data available"}), 400
    logger.debug(f"API data requested for sid={sid}")
    return jsonify(STORE[sid])

@app.route("/results/pdf")
def export_pdf():
    sid = session.get("sid")
    if not sid or sid not in STORE:
        flash(gettext("No data available. Please login first."), "warning")
        return redirect(url_for("index"))

    data = STORE[sid]

    # Build subjects list from subject averages ONLY, sorted alphabetically by subject name
    subjects = []
    subject_averages = data.get("subject_averages", {}) or {}
    for name in sorted(subject_averages.keys(), key=lambda s: s.lower()):
        avg = float(subject_averages.get(name, 0) or 0)
        subjects.append({
            "name": name,
            "score": round(avg, 2),
        })

    logger.info(f"Generating PDF for sid={sid}, student={data.get('student_name')}")
    rendered = render_template(
        "results_pdf.html",
        year=data.get("year", ""),
        student_name=data.get("student_name", "-"),
        class_name=data.get("class_name", "-"),
        date=data.get("date", ""),
        subjects=subjects,
        average_20=data.get("brevet_stats", {}).get("moyenne_sur_20", 0),
        socle_400=data.get("brevet_stats", {}).get("socle_sur_400", 0),
    )

    pdf_io = BytesIO()
    HTML(string=rendered, base_url=request.host_url).write_pdf(pdf_io)
    pdf_io.seek(0)
    logger.info(f"PDF generation complete for sid={sid}, bytes={pdf_io.getbuffer().nbytes}")
    return send_file(
        pdf_io,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="brevet_report.pdf"
    )

if __name__ == "__main__":
    # For local testing only; Railway uses Gunicorn
    app.run(host="0.0.0.0", port=5000, debug=True)
