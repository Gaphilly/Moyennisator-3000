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

from flask import Flask, render_template, request, flash, redirect, url_for, jsonify
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length

try:
    from pronotepy import Client
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Error: Missing required dependencies. Please install: {e}")
    print("Run: pip install pronotepy python-dotenv flask flask-wtf")
    exit(1)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('pronote_web.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Flask app configuration
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['WTF_CSRF_ENABLED'] = False  # Disabled for simplicity

class LoginForm(FlaskForm):
    """Form for Pronote login credentials."""
    username = StringField('Username', 
                          validators=[DataRequired(), Length(min=1, max=50)],
                          render_kw={"placeholder": "Enter your Pronote username"})
    password = PasswordField('Password', 
                           validators=[DataRequired(), Length(min=1, max=100)],
                           render_kw={"placeholder": "Enter your Pronote password"})
    submit = SubmitField('Analyze Grades')

class PronoteAnalyzer:
    """Main class for analyzing Pronote academic data."""
    
    def __init__(self):
        self.client: Optional[Client] = None
        self.evaluations: List[Dict] = []
        self.fixed_url = "https://4170004n.index-education.net/pronote/eleve.html"
    
    def connect_to_pronote(self, username: str, password: str) -> Tuple[bool, str]:
        """
        Establish connection to Pronote.
        
        Args:
            username: Username
            password: Password
            
        Returns:
            Tuple of (success, message)
        """
        try:
            logger.info("Attempting to connect to Pronote...")
            self.client = Client(self.fixed_url, username, password)
            
            if not self.client.logged_in:
                logger.error("Login failed. Please check your credentials.")
                return False, "Login failed. Please check your credentials."
            
            logger.info("Successfully connected to Pronote")
            return True, "Successfully connected to Pronote"
            
        except Exception as e:
            error_msg = f"Connection error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def grade_to_points(self, grade: str) -> int:
        """
        Convert letter grade to points for Brevet calculation.
        
        Args:
            grade: Letter grade from pronotepy (A+, A, C, E)
            
        Returns:
            Points value
        """
        mapping = {
            "A+": 50,
            "A": 40, 
            "C": 25,
            "E": 10
        }
        return mapping.get(grade, 0)
    
    def convert_grade_for_display(self, grade: str) -> str:
        """
        Convert pronotepy grades (A+, A, C, E) to French display grades (V+, V, J, R).
        
        Args:
            grade: Raw grade from pronotepy
            
        Returns:
            French equivalent for display
        """
        mapping = {
            "A+": "V+",
            "A": "V",
            "C": "J", 
            "E": "R"
        }
        return mapping.get(grade, grade)  # Return original if not in mapping
    
    def fetch_evaluations(self) -> Tuple[bool, str]:
        """
        Fetch all evaluations from Pronote.
        
        Returns:
            Tuple of (success, message)
        """
        if not self.client or not self.client.logged_in:
            return False, "Not connected to Pronote"
        
        try:
            logger.info("Fetching evaluations...")
            self.evaluations = []
            
            periods = getattr(self.client, 'periods', [])
            if not periods:
                logger.warning("No periods found")
                return True, "No periods found"
            
            for period in periods:
                evaluations = getattr(period, 'evaluations', [])
                for evaluation in evaluations:
                    try:
                        eval_dict = self._process_evaluation(evaluation)
                        if eval_dict:
                            self.evaluations.append(eval_dict)
                    except Exception as e:
                        logger.warning(f"Error processing evaluation: {str(e)}")
                        continue
            
            logger.info(f"Successfully fetched {len(self.evaluations)} evaluations")
            return True, f"Successfully fetched {len(self.evaluations)} evaluations"
            
        except Exception as e:
            error_msg = f"Error fetching evaluations: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def _process_evaluation(self, evaluation) -> Optional[Dict]:
        """
        Process a single evaluation.
        
        Args:
            evaluation: Evaluation object from Pronote
            
        Returns:
            Dictionary with evaluation data or None if invalid
        """
        try:
            acquisitions = getattr(evaluation, 'acquisitions', [])
            
            # Calculate points for each acquisition
            points = []
            grades = []
            for acq in acquisitions:
                raw_grade = getattr(acq, 'abbreviation', '')
                if raw_grade:
                    # Convert to French display format for users
                    display_grade = self.convert_grade_for_display(raw_grade)
                    grades.append(display_grade)
                    # Use raw grade for calculation
                    points.append(self.grade_to_points(raw_grade))
            
            # Calculate average points
            avg_points = sum(points) / len(points) if points else 0
            
            # Get subject name
            subject_obj = getattr(evaluation, 'subject', None)
            subject_name = getattr(subject_obj, 'name', 'Unknown') if subject_obj else 'Unknown'
            
            # Create evaluation dictionary
            eval_dict = {
                "subject": subject_name,
                "date": str(getattr(evaluation, 'date', 'Unknown')),
                "name": getattr(evaluation, 'name', 'Unnamed'),
                "coefficient": getattr(evaluation, 'coefficient', 1),
                "grades": grades,
                "average_points": round(avg_points, 2)
            }
            
            return eval_dict
            
        except Exception as e:
            logger.warning(f"Error processing individual evaluation: {str(e)}")
            return None
    
    def calculate_subject_averages(self) -> Dict[str, float]:
        """
        Calculate weighted averages by subject.
        
        Returns:
            Dictionary with subject averages
        """
        if not self.evaluations:
            return {}
        
        subject_totals = defaultdict(lambda: {"points": 0, "coeffs": 0})
        
        for eval_data in self.evaluations:
            subject = eval_data["subject"]
            coeff = eval_data.get("coefficient", 0)
            avg_points = eval_data.get("average_points", 0)
            
            subject_totals[subject]["points"] += avg_points * coeff
            subject_totals[subject]["coeffs"] += coeff
        
        # Calculate averages
        subject_averages = {}
        for subject, values in subject_totals.items():
            if values["coeffs"] > 0:
                subject_averages[subject] = round(values["points"] / values["coeffs"], 2)
            else:
                subject_averages[subject] = 0
        
        return subject_averages
    
    def compute_brevet_stats(self) -> Dict[str, float]:
        """
        Compute Brevet statistics from evaluation data.
        
        Returns:
            Dictionary with Brevet statistics
        """
        if not self.evaluations:
            return {
                "moyenne_points": 0,
                "moyenne_sur_20": 0,
                "socle_sur_400": 0
            }
        
        # Calculate total coefficients and points
        total_coeff = sum(item.get("coefficient", 0) for item in self.evaluations)
        if total_coeff == 0:
            return {
                "moyenne_points": 0,
                "moyenne_sur_20": 0,
                "socle_sur_400": 0
            }
        
        total_points = sum(
            item.get("coefficient", 0) * item.get("average_points", 0) 
            for item in self.evaluations
        )
        
        # Calculate statistics
        moyenne_points = total_points / total_coeff
        moyenne_sur_20 = moyenne_points * 0.4
        socle_sur_400 = moyenne_points * 8
        
        return {
            "moyenne_points": round(moyenne_points, 2),
            "moyenne_sur_20": round(moyenne_sur_20, 2),
            "socle_sur_400": round(socle_sur_400, 2)
        }
    
    def get_performance_level(self, socle_score: float) -> str:
        """Get performance level description based on socle score."""
        if socle_score >= 350:
            return "Excellent (Mention Très Bien possible)"
        elif socle_score >= 280:
            return "Good (Mention Bien possible)"
        elif socle_score >= 240:
            return "Satisfactory (Mention Assez Bien possible)"
        elif socle_score >= 200:
            return "Pass level"
        else:
            return "Below pass level"

# Global analyzer instance
analyzer = PronoteAnalyzer()

@app.route('/', methods=['GET', 'POST'])
def index():
    """Main page with login form."""
    form = LoginForm()
    
    if request.method == 'POST':
        logger.info(f"Form validation: {form.validate()}")
        logger.info(f"Form errors: {form.errors}")
        
        if form.validate_on_submit():
            username = form.username.data
            password = form.password.data
            
            logger.info(f"Processing login for user: {username}")
            
            # Connect to Pronote
            success, message = analyzer.connect_to_pronote(str(username), str(password))
            
            if not success:
                flash(f'Connection failed: {message}', 'error')
                return render_template('index.html', form=form)
            
            # Fetch evaluations
            success, message = analyzer.fetch_evaluations()
            
            if not success:
                flash(f'Error fetching data: {message}', 'error')
                return render_template('index.html', form=form)
            
            flash(f'Success: {message}', 'success')
            return redirect(url_for('results'))
        else:
            # Form validation failed
            flash('Please check your input and try again.', 'error')
    
    return render_template('index.html', form=form)

@app.route('/results')
def results():
    """Results page showing grade analysis."""
    if not analyzer.evaluations:
        flash('No data available. Please login first.', 'warning')
        return redirect(url_for('index'))
    
    # Calculate statistics
    subject_averages = analyzer.calculate_subject_averages()
    brevet_stats = analyzer.compute_brevet_stats()
    performance_level = analyzer.get_performance_level(brevet_stats['socle_sur_400'])
    
    return render_template('results.html', 
                         evaluations=analyzer.evaluations,
                         subject_averages=subject_averages,
                         brevet_stats=brevet_stats,
                         performance_level=performance_level,
                         total_evaluations=len(analyzer.evaluations))

@app.route('/api/data')
def api_data():
    """API endpoint for JSON data."""
    if not analyzer.evaluations:
        return jsonify({'error': 'No data available'}), 400
    
    return jsonify({
        'evaluations': analyzer.evaluations,
        'subject_averages': analyzer.calculate_subject_averages(),
        'brevet_stats': analyzer.compute_brevet_stats(),
        'total_evaluations': len(analyzer.evaluations)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)