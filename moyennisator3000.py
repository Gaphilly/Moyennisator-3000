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

    def connect_and_fetch(self, username: str, password: str) -> Tuple[bool, str, List[Dict], Dict, List]:
        """
        Connect, fetch evaluations, then dispose of the client.
        Returns (success, message, evaluations_processed, info_dict, evaluations_raw)
        """
        client: Optional[Client] = None
        try:
            logger.info(f"Attempting to connect to Pronote as {username} with password {password}...")
            # Use the fixed_url attribute unless overridden in this call via thread-local/session
            pronote_url = getattr(self, 'fixed_url', None) or self.fixed_url
            client = Client(pronote_url, username, password)

            if not getattr(client, "logged_in", False):
                logger.error("Login failed. Please check your credentials.")
                return False, gettext("Login failed. Please check your credentials."), [], {}, []

            logger.info(f"Successfully connected to Pronote!")

            # Fetch evaluations immediately, don't store client anywhere
            evaluations_processed: List[Dict] = []
            evaluations_raw: List = []
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
                return True, gettext("No periods found"), [], info, []

            for period in periods:
                for evaluation in getattr(period, "evaluations", []):
                    try:
                        evaluations_raw.append(evaluation)
                        ev = self._process_evaluation(evaluation)
                        if ev:
                            evaluations_processed.append(ev)
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

            logger.info(f"Successfully fetched {len(evaluations_processed)} evaluations")
            return True, gettext("Successfully fetched %(n)d evaluations", n=len(evaluations_processed)), evaluations_processed, info, evaluations_raw

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

    def grade_abbreviation_to_palier(self, abbrev: str) -> int:
        """Convert abbreviation (A+, A, C, E) to palier points (50, 40, 25, 10)."""
        mapping = {"A+": 50, "A": 40, "C": 25, "E": 10}
        return mapping.get(abbrev, 0)

    def convert_grade_for_display(self, grade: str) -> str:
        mapping = {"A+": "V+", "A": "V", "C": "J", "E": "R"}
        return mapping.get(grade, grade)

    def count_domains_and_acquisitions(self, evaluations_raw: List) -> Tuple[Dict, List]:
        """
        Count acquisitions per domain and track DNL HG acquisitions.
        Only tracks acquisitions with empty pillar_prefix where subject is exactly "DNL HG".
        Returns (domain_counts: Dict[subdomain -> count], dnl_hg_acquisitions: List[acq dicts])
        """
        domain_counts = defaultdict(int)
        dnl_hg_acquisitions = []

        for evaluation in evaluations_raw:
            eval_subject = getattr(getattr(evaluation, "subject", None), "name", "Unknown")
            
            for acq in getattr(evaluation, "acquisitions", []):
                pillar = getattr(acq, "pillar_prefix", "")
                abbrev = getattr(acq, "abbreviation", "")
                
                if pillar:
                    # Split comma-separated domains
                    for subdomain in [s.strip() for s in pillar.split(',')]:
                        domain_counts[subdomain] += 1
                else:
                    # Only track DNL HG acquisitions (ignore other empty pillar_prefix)
                    if eval_subject == "DNL HG":
                        dnl_hg_acquisitions.append({
                            "evaluation_name": getattr(evaluation, "name", "Unknown"),
                            "subject": eval_subject,
                            "abbreviation": abbrev,
                            "palier_points": self.grade_abbreviation_to_palier(abbrev),
                        })

        return dict(domain_counts), dnl_hg_acquisitions

    def compute_domain_scores(self, evaluations_raw: List) -> Dict:
        """
        Compute per-domain scores based on threshold-snapping of average palier points.
        Each domain gets one of: V+ (50), V (40), J (25), R (10) based on average points.
        Returns dict: { domain_name: { "count": int, "avg_points_50": float, "avg_points_20": float, "palier": str } }
        """
        domain_palier_points = defaultdict(list)  # domain -> [points, points, ...]
        
        # Collect palier points per domain
        for evaluation in evaluations_raw:
            for acq in getattr(evaluation, "acquisitions", []):
                pillar = getattr(acq, "pillar_prefix", "")
                abbrev = getattr(acq, "abbreviation", "")
                
                if pillar:
                    palier_points = self.grade_abbreviation_to_palier(abbrev)
                    for subdomain in [s.strip() for s in pillar.split(',')]:
                        domain_palier_points[subdomain].append(palier_points)

        # Compute average points per domain and snap to nearest palier
        domain_scores = {}
        for domain, points_list in domain_palier_points.items():
            avg_points = sum(points_list) / len(points_list)
            
            # Threshold-based snapping:
            # >= 45 → V+ (50), >= 32.5 → V (40), >= 17.5 → J (25), < 17.5 → R (10)
            if avg_points >= 45:
                snapped_points = 50
                palier_display = "V+"
            elif avg_points >= 32.5:
                snapped_points = 40
                palier_display = "V"
            elif avg_points >= 17.5:
                snapped_points = 25
                palier_display = "J"
            else:
                snapped_points = 10
                palier_display = "R"
            
            snapped_points_20 = round(snapped_points * 0.4, 2)
            
            domain_scores[domain] = {
                "count": len(points_list),
                "avg_points_50": float(snapped_points),
                "avg_points_20": snapped_points_20,
                "palier": palier_display,
                "raw_avg": round(avg_points, 2),  # For debugging
            }

        return domain_scores

    def _process_evaluation(self, evaluation) -> Optional[Dict]:
        try:
            acquisitions = getattr(evaluation, "acquisitions", [])
            points: List[int] = []
            grades: List[str] = []

            for acq in acquisitions:
                raw = getattr(acq, "abbreviation", "")
                if raw:
                    grades.append(self.convert_grade_for_display(raw))
                    points.append(self.grade_abbreviation_to_palier(raw))

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

    def compute_brevet_stats(self, evaluations_raw: List) -> Dict[str, any]:
            """
            Compute DNB domain scores and overall statistics.
            Returns dict with domain_scores, total_50, total_400, dnl_hg_acquisitions.
            DNB has exactly 8 official domains: D1.1, D1.2, D1.3, D1.4, D2, D3, D4, D5
            Total /400 = sum of all 8 domain scores (/50 each).
            DNL HG acquisitions are tracked separately and don't count towards /400.
            """
            if not evaluations_raw:
                return {
                    "domain_scores": {},
                    "total_50": 0,
                    "total_400": 0,
                    "dnl_hg_acquisitions": [],
                }

            domain_scores = self.compute_domain_scores(evaluations_raw)
            domain_counts, dnl_hg_acquisitions = self.count_domains_and_acquisitions(evaluations_raw)

            # Compute a single snapped DNL HG score (do not include in /400)
            dnl_hg_score = None
            if dnl_hg_acquisitions:
                pts = [a.get("palier_points", 0) for a in dnl_hg_acquisitions]
                raw_avg = sum(pts) / len(pts) if pts else 0.0
                # snap thresholds
                if raw_avg >= 45:
                    snapped = 50
                    pal = "V+"
                elif raw_avg >= 32.5:
                    snapped = 40
                    pal = "V"
                elif raw_avg >= 17.5:
                    snapped = 25
                    pal = "J"
                else:
                    snapped = 10
                    pal = "R"

                dnl_hg_score = {
                    "count": len(pts),
                    "avg_points_50": float(snapped),
                    "avg_points_20": round(snapped * 0.4, 2),
                    "palier": pal,
                    "raw_avg": round(raw_avg, 2),
                }

            # Filter out EMPTY and compute totals
            official_domains = [d for d in domain_scores.keys() if d != "EMPTY"]
            if official_domains:
                # Total /400 = direct sum of all 8 domain scores (each domain is /50)
                total_400 = sum(domain_scores[d]["avg_points_50"] for d in official_domains)
            else:
                total_400 = 0

            return {
                "domain_scores": domain_scores,
                "total_50": round(total_400 / 8 if official_domains else 0, 2),
                "total_400": round(total_400, 2),
                "dnl_hg_acquisitions": dnl_hg_acquisitions,
                "dnl_hg_score": dnl_hg_score,
            }

    def get_performance_level(self, total_400: float) -> str:
        """Determine performance level based on total /400 score (sum of 8 domains /50)."""
        if total_400 >= 360:
            return gettext("Outstanding (Mention Très Bien avec Félicitations)")
        elif total_400 >= 320:
            return gettext("Excellent (Mention Très Bien possible)")
        elif total_400 >= 280:
            return gettext("Good (Mention Bien possible)")
        elif total_400 >= 240:
            return gettext("Satisfactory (Mention Assez Bien possible)")
        elif total_400 >= 200:
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

            success, message, evaluations, info, evaluations_raw = analyzer.connect_and_fetch(username, password)
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
            brevet_stats = analyzer.compute_brevet_stats(evaluations_raw)
            performance_level = analyzer.get_performance_level(brevet_stats.get("total_400", 0))
            logger.info(f"Computed stats: {len(evaluations_sorted)} evaluations, total_400={brevet_stats.get('total_400')}")

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


# Auto-login endpoint (used by the PWA). Expects JSON: { username, password_sha256, pronote_url_select?, pronote_url_custom? }
# This route is intentionally CSRF-exempt because it's called from the PWA runtime JS using fetch.
@csrf.exempt
@app.route('/auto_login', methods=['POST'])
def auto_login():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": gettext("Invalid request")}), 400

    if not data:
        return jsonify({"error": gettext("Invalid request")}), 400

    username = str(data.get('username') or '').strip()
    password_sha256 = str(data.get('password_sha256') or '')

    if not username or not password_sha256:
        return jsonify({"error": gettext("Missing credentials")}), 400

    # determine chosen_url same as in index()
    selected_url = data.get('pronote_url_select')
    custom_url = (data.get('pronote_url_custom') or '').strip()
    if selected_url == 'other' and custom_url:
        chosen_url = custom_url
    else:
        chosen_url = None
        for p in DEFAULT_URL_PRESETS:
            if p.get('url') == selected_url or p.get('name') == selected_url:
                chosen_url = p.get('url')
                break
        if not chosen_url:
            chosen_url = selected_url

    # minimal URL validation (same relaxed check as index)
    try:
        parsed = urlparse(str(chosen_url))
        path = str(parsed.path or '')
        normalized_path = path.rstrip('/')
        if not normalized_path.endswith('/pronote') and not normalized_path.endswith('/eleve.html'):
            return jsonify({"error": gettext("Please select or enter a valid Pronote URL that ends with /pronote or /eleve.html.")}), 400
    except Exception:
        return jsonify({"error": gettext("Please select or enter a valid Pronote URL that ends with /pronote or /eleve.html.")}), 400

    # Ensure session id
    if 'sid' not in session:
        session['sid'] = str(uuid4())

    analyzer.fixed_url = str(chosen_url)
    logger.info(f"Auto-login attempt for user {username} against {analyzer.fixed_url}")

    # NOTE: the client provides a SHA256(password) and we forward that string as the password to Pronotepy.
    # This keeps no plaintext stored on the server. If the remote service requires the raw password this
    # may fail and we'll return an error to the client which will clear its local storage.
    success, message, evaluations, info, evaluations_raw = analyzer.connect_and_fetch(username, password_sha256)
    if not success:
        logger.info(f"Auto-login failed for user {username}: {message}")
        return jsonify({"error": message}), 401

    evaluations_sorted = sorted(
        evaluations,
        key=lambda e: e.get("date_obj") or date.min,
        reverse=True
    )

    subject_averages = analyzer.calculate_subject_averages(evaluations_sorted)
    brevet_stats = analyzer.compute_brevet_stats(evaluations_raw)
    performance_level = analyzer.get_performance_level(brevet_stats.get("total_400", 0))

    STORE[session["sid"]] = {
        "evaluations": evaluations_sorted,
        "subject_averages": subject_averages,
        "brevet_stats": brevet_stats,
        "performance_level": performance_level,
        "total_evaluations": len(evaluations_sorted),
        "date": datetime.now().strftime("%d/%m/%Y"),
        "year": datetime.now().year,
        "student_name": (info.get("student_name") if isinstance(info, dict) else "-") if 'info' in locals() else "-",
        "class_name": (info.get("class_name") if isinstance(info, dict) else "-") if 'info' in locals() else "-",
    }

    logger.info(f"Auto-login success for sid={session['sid']} user={username}")
    return jsonify({"redirect": url_for('results')}), 200

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

@app.route('/d8848862cac0447f833f83d1c3afcae3.txt')
def download_specific_file():
    file_path = os.path.join(app.root_path, 'd8848862cac0447f833f83d1c3afcae3.txt')
    return send_file(file_path)

@app.route("/results/pdf")
def export_pdf():
    sid = session.get("sid")
    if not sid or sid not in STORE:
        flash(gettext("No data available. Please login first."), "warning")
        return redirect(url_for("index"))

    data = STORE[sid]
    brevet_stats = data.get("brevet_stats", {})

    # Build subjects list from domain_scores (only official domains, not EMPTY)
    subjects = []
    domain_scores = brevet_stats.get("domain_scores", {}) or {}
    for domain_name in sorted([d for d in domain_scores.keys() if d != 'EMPTY']):
        score_data = domain_scores[domain_name]
        subjects.append({
            "name": domain_name,
            "score_50": score_data.get("avg_points_50", 0),
            "score_20": score_data.get("avg_points_20", 0),
        })

    performance_level = data.get("performance_level", gettext("Unknown"))

    logger.info(f"Generating PDF for sid={sid}, student={data.get('student_name')}")
    rendered = render_template(
        "results_pdf.html",
        year=data.get("year", ""),
        student_name=data.get("student_name", "-"),
        class_name=data.get("class_name", "-"),
        date=data.get("date", ""),
        subjects=subjects,
        total_400=brevet_stats.get("total_400", 0),
        performance_level=performance_level,
        )
    # --- Patch : ne pas passer target=pdf_io, récupérer les bytes directement ---
    pdf_bytes = HTML(string=rendered, base_url=request.url_root).write_pdf()  # retourne les bytes
    pdf_io = BytesIO(pdf_bytes)  # convertir en BytesIO pour send_file

    return send_file(
        pdf_io,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="brevet_report.pdf"
        )

if __name__ == "__main__":
    # For local testing only; Railway uses Gunicorn
    app.run(host="0.0.0.0", port=5000, debug=True)
