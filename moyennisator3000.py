#!/usr/bin/env python3
"""
Pronote Grade Analyzer - Web Application
A secure web interface for analyzing French academic grades from Pronote 
with Brevet statistics calculation.
"""

import os
import logging
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from uuid import uuid4

from flask import Flask, render_template, request, flash, redirect, url_for, jsonify, session
from flask_babel import Babel, gettext
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length

try:
    from pronotepy import Client
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Error: Missing required dependencies. Please install: {e}")
    print("Run: pip install pronotepy python-dotenv flask flask-wtf flask-babel wtforms")
    exit(1)

# ---------- Config & Logging ----------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("pronote_web.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key-change-in-production")
app.config["WTF_CSRF_ENABLED"] = False  # Disabled for simplicity, your call

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

    def connect_and_fetch(self, username: str, password: str) -> Tuple[bool, str, List[Dict]]:
        """
        Connect, fetch evaluations, then dispose of the client.
        Returns (success, message, evaluations)
        """
        client: Optional[Client] = None
        try:
            logger.info("Attempting to connect to Pronote...")
            client = Client(self.fixed_url, username, password)

            if not getattr(client, "logged_in", False):
                logger.error("Login failed. Please check your credentials.")
                return False, gettext("Login failed. Please check your credentials."), []

            logger.info("Successfully connected to Pronote")

            # Fetch evaluations immediately, don't store client anywhere
            evaluations: List[Dict] = []
            periods = getattr(client, "periods", [])
            if not periods:
                logger.warning("No periods found")
                return True, gettext("No periods found"), []

            for period in periods:
                for evaluation in getattr(period, "evaluations", []):
                    try:
                        ev = self._process_evaluation(evaluation)
                        if ev:
                            evaluations.append(ev)
                    except Exception as e:
                        logger.warning(f"Error processing evaluation: {e}")
                        continue

            logger.info(f"Successfully fetched {len(evaluations)} evaluations")
            return True, gettext("Successfully fetched %(n)d evaluations", n=len(evaluations)), evaluations

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
            return False, error_msg, []
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

            return {
                "subject": subject_name,
                "date": str(getattr(evaluation, "date", "Unknown")),
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
        for subject, v in totals.items():
            out[subject] = round((v["points"] / v["coeffs"]) if v["coeffs"] > 0 else 0.0, 2)
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

            logger.info(f"Processing login for user: {username}")

            success, message, evaluations = analyzer.connect_and_fetch(username, password)
            if not success:
                flash(gettext("Connection failed: %(message)s", message=message), "error")
                return render_template("index.html", form=form)

            # Compute and stash per-session results in memory
            subject_averages = analyzer.calculate_subject_averages(evaluations)
            brevet_stats = analyzer.compute_brevet_stats(evaluations)
            performance_level = analyzer.get_performance_level(brevet_stats["socle_sur_400"])

            STORE[session["sid"]] = {
                "evaluations": evaluations,
                "subject_averages": subject_averages,
                "brevet_stats": brevet_stats,
                "performance_level": performance_level,
                "total_evaluations": len(evaluations),
            }

            flash(gettext("Success: %(message)s", message=message), "success")
            return redirect(url_for("results"))
        else:
            flash(gettext("Please check your input and try again."), "error")

    return render_template("index.html", form=form)

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
    return jsonify(STORE[sid])

if __name__ == "__main__":
    # For local testing only; Railway uses Gunicorn
    app.run(host="0.0.0.0", port=5000)
