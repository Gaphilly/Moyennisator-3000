#!/usr/bin/env python3
"""
Pronote Grade Analyzer
A secure Python script for analyzing French academic grades from Pronote 
with Brevet statistics calculation.
"""

import os
import sys
import logging
import getpass
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

try:
    from pronotepy import Client
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Error: Missing required dependencies. Please install: {e}")
    print("Run: pip install pronotepy python-dotenv")
    sys.exit(1)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('pronote_analyzer.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class PronoteAnalyzer:
    """Main class for analyzing Pronote academic data."""
    
    def __init__(self):
        self.client: Optional[Client] = None
        self.evaluations: List[Dict] = []
    
    def get_credentials(self) -> Tuple[str, str, str]:
        """
        Get Pronote credentials from environment variables or user input.
        
        Returns:
            Tuple of (url, username, password)
        """
        # Try to get from environment variables first
        url = os.getenv('PRONOTE_URL')
        username = os.getenv('PRONOTE_USERNAME')
        password = os.getenv('PRONOTE_PASSWORD')
        
        # Use fixed URL if not in environment
        if not url:
            url = "https://4170004n.index-education.net/pronote/eleve.html"
            logger.info(f"Using default Pronote URL: {url}")
        
        if not username:
            username = input("Enter username: ").strip()
            if not username:
                raise ValueError("Username is required")
        
        if not password:
            password = getpass.getpass("Enter password: ")
            if not password:
                raise ValueError("Password is required")
        
        return url, username, password
    
    def connect_to_pronote(self, url: str, username: str, password: str) -> bool:
        """
        Establish connection to Pronote.
        
        Args:
            url: Pronote URL
            username: Username
            password: Password
            
        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info("Attempting to connect to Pronote...")
            self.client = Client(url, username, password)
            
            if not self.client.logged_in:
                logger.error("Login failed. Please check your credentials.")
                return False
            
            logger.info("Successfully connected to Pronote")
            return True
            
        except Exception as e:
            logger.error(f"Connection error: {str(e)}")
            return False
    
    def grade_to_points(self, grade: str) -> int:
        """
        Convert letter grade to points for Brevet calculation.
        
        Args:
            grade: Letter grade (A+, A, C, E, etc.)
            
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
    
    def fetch_evaluations(self) -> bool:
        """
        Fetch all evaluations from Pronote.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.client or not self.client.logged_in:
            logger.error("Not connected to Pronote")
            return False
        
        try:
            logger.info("Fetching evaluations...")
            self.evaluations = []
            
            periods = getattr(self.client, 'periods', [])
            if not periods:
                logger.warning("No periods found")
                return True
            
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
            return True
            
        except Exception as e:
            logger.error(f"Error fetching evaluations: {str(e)}")
            return False
    
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
                grade = getattr(acq, 'abbreviation', '')
                if grade:
                    grades.append(grade)
                    points.append(self.grade_to_points(grade))
            
            # Calculate average points
            avg_points = sum(points) / len(points) if points else 0
            
            # Get subject name
            subject_obj = getattr(evaluation, 'subject', None)
            subject_name = getattr(subject_obj, 'name', 'Unknown') if subject_obj else 'Unknown'
            
            # Create evaluation dictionary
            eval_dict = {
                "subject": subject_name,
                "date": getattr(evaluation, 'date', None),
                "name": getattr(evaluation, 'name', 'Unnamed'),
                "coefficient": getattr(evaluation, 'coefficient', 1),
                "grades": grades,
                "average_points": avg_points
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
            logger.warning("No evaluations available for calculation")
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
                subject_averages[subject] = values["points"] / values["coeffs"]
            else:
                subject_averages[subject] = 0
        
        return subject_averages
    
    def compute_brevet_stats(self, data: List[Dict]) -> Dict[str, float]:
        """
        Compute Brevet statistics from evaluation data.
        
        Args:
            data: List of evaluation dictionaries
            
        Returns:
            Dictionary with Brevet statistics
        """
        if not data:
            return {
                "moyenne_points": 0,
                "moyenne_sur_20": 0,
                "socle_sur_400": 0
            }
        
        # Calculate total coefficients and points
        total_coeff = sum(item.get("coefficient", 0) for item in data)
        if total_coeff == 0:
            return {
                "moyenne_points": 0,
                "moyenne_sur_20": 0,
                "socle_sur_400": 0
            }
        
        total_points = sum(
            item.get("coefficient", 0) * item.get("average_points", 0) 
            for item in data
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
    
    def display_results(self):
        """Display formatted results."""
        print("\n" + "="*60)
        print("           PRONOTE GRADE ANALYSIS RESULTS")
        print("="*60)
        
        if not self.evaluations:
            print("\nNo evaluations found or processed.")
            return
        
        # Individual evaluations
        print(f"\n--- INDIVIDUAL EVALUATIONS ({len(self.evaluations)} total) ---")
        print("-" * 60)
        
        for i, eval_data in enumerate(self.evaluations, 1):
            print(f"\n{i}. Subject: {eval_data['subject']}")
            print(f"   Name: {eval_data['name']}")
            print(f"   Date: {eval_data['date']}")
            print(f"   Coefficient: {eval_data['coefficient']}")
            print(f"   Grades: {', '.join(eval_data['grades']) if eval_data['grades'] else 'No grades'}")
            print(f"   Average Points: {eval_data['average_points']:.2f}")
        
        # Subject averages
        subject_averages = self.calculate_subject_averages()
        print(f"\n--- SUBJECT AVERAGES (WEIGHTED) ---")
        print("-" * 40)
        
        if subject_averages:
            for subject, avg in sorted(subject_averages.items()):
                print(f"{subject:.<30} {avg:.2f} points")
        else:
            print("No subject averages calculated.")
        
        # Brevet statistics
        brevet_stats = self.compute_brevet_stats(self.evaluations)
        print(f"\n--- BREVET STATISTICS ---")
        print("-" * 25)
        print(f"Average Points:     {brevet_stats['moyenne_points']:.2f}/50")
        print(f"Average (out of 20): {brevet_stats['moyenne_sur_20']:.2f}/20")
        print(f"Socle Score (400):   {brevet_stats['socle_sur_400']:.2f}/400")
        
        # Interpretation
        socle_score = brevet_stats['socle_sur_400']
        if socle_score >= 350:
            level = "Excellent (Mention Très Bien possible)"
        elif socle_score >= 280:
            level = "Good (Mention Bien possible)"
        elif socle_score >= 240:
            level = "Satisfactory (Mention Assez Bien possible)"
        elif socle_score >= 200:
            level = "Pass level"
        else:
            level = "Below pass level"
        
        print(f"Performance Level:   {level}")
        print("\n" + "="*60)

def main():
    """Main function to run the Pronote analyzer."""
    analyzer = PronoteAnalyzer()
    
    try:
        # Get credentials
        logger.info("Starting Pronote Grade Analyzer")
        url, username, password = analyzer.get_credentials()
        
        # Connect to Pronote
        if not analyzer.connect_to_pronote(url, username, password):
            logger.error("Failed to connect to Pronote")
            sys.exit(1)
        
        # Fetch evaluations
        if not analyzer.fetch_evaluations():
            logger.error("Failed to fetch evaluations")
            sys.exit(1)
        
        # Display results
        analyzer.display_results()
        
        logger.info("Analysis completed successfully")
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
