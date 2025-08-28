# replit.md

## Overview

The Pronote Grade Analyzer is a Python application designed to analyze French academic grades from the Pronote educational platform. The system connects to Pronote's API to retrieve student evaluation data, processes grades using the French grading system, and calculates Brevet (French diploma) statistics. The application focuses on secure credential management, comprehensive error handling, and clean data presentation for academic performance analysis.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Core Application Structure
- **Single Script Architecture**: Built as a standalone Python script (`pronote_analyzer.py`) with a main `PronoteAnalyzer` class
- **Object-Oriented Design**: Uses class-based architecture for managing Pronote connections and data processing
- **Modular Functions**: Separates concerns with dedicated functions for credential management, grade conversion, and statistics calculation

### Authentication & Security
- **Environment Variable Support**: Integrates with python-dotenv for secure credential storage
- **Fallback Input System**: Provides secure user input via getpass module when environment variables aren't available
- **Credential Isolation**: Keeps sensitive login information separate from code logic

### Grade Processing System
- **French Grade Mapping**: Implements specific conversion system (A+ = 50 points, A = 40 points, C = 25 points, E = 10 points)
- **Coefficient-Based Calculations**: Processes weighted averages using evaluation coefficients
- **Brevet Statistics**: Calculates French diploma scoring with multiple metrics (moyenne_points, moyenne_sur_20, socle_sur_400)

### Data Management
- **In-Memory Processing**: Uses Python collections (defaultdict, lists) for temporary data storage
- **Structured Data Types**: Leverages typing annotations for clear data contracts
- **Evaluation Aggregation**: Groups and processes evaluations by subject and time period

### Logging & Error Handling
- **Dual Logging**: Outputs to both file (pronote_analyzer.log) and console
- **Comprehensive Error Handling**: Includes dependency checking and connection validation
- **Structured Logging**: Uses Python's logging module with formatted timestamps and levels

## External Dependencies

### Core Dependencies
- **pronotepy**: Official Python library for Pronote API integration - handles authentication and data retrieval from French academic platform
- **python-dotenv**: Environment variable management for secure credential storage

### Standard Library Components
- **logging**: Application logging and error tracking
- **getpass**: Secure password input without echo
- **collections**: Data structure utilities (defaultdict for subject groupings)
- **typing**: Type annotations for better code clarity and IDE support
- **os/sys**: System integration and environment variable access

### Academic Platform Integration
- **Pronote API**: Connects to French educational platform via HTTPS endpoints
- **Authentication System**: Handles session management and credential validation through pronotepy client
- **Data Retrieval**: Accesses student evaluations, grades, and academic periods through official API channels