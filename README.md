# Respiratory Health Awareness Backend

Backend API for the **Respiratory Health Awareness and Prediction System**.

The system accepts respiratory symptom descriptions in English, Kiswahili, or mixed-language text, maps the text to recognised symptoms, allows symptom confirmation, checks for red-flag symptoms, and uses a trained Random Forest model to return the top three possible condition patterns.

The system is designed for **health-awareness purposes only** and does not provide a medical diagnosis.

---

## Features

The backend provides:

- FastAPI REST API
- English and Kiswahili symptom mapping
- Mixed-language symptom input
- Fuzzy symptom matching
- Symptom confirmation and rejection
- Red-flag symptom detection
- Safety-only red-flag handling
- Random Forest prediction
- Top-3 condition pattern results
- User-friendly disease names
- Plain-English condition descriptions
- Model-confidence explanation
- Health-awareness guidance
- PostgreSQL prediction history
- User feedback storage
- Duplicate feedback protection
- API validation and error handling
- Automated backend tests

---

## Technology Stack

- Python 3.11
- FastAPI
- Uvicorn
- Pydantic
- scikit-learn
- pandas
- NumPy
- joblib
- RapidFuzz
- SQLAlchemy
- PostgreSQL
- Neon PostgreSQL
- Psycopg
- pytest
- HTTPX

---

## Project Structure

```text
ucs31-madhaparia-backend/
│
├── app/
│   ├── core/
│   │   ├── config.py
│   │   └── database.py
│   │
│   ├── data/
│   │   └── condition_information.json
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   └── models.py
│   │
│   ├── ml/
│   │   ├── artifacts/
│   │   │   ├── disease_classes.json
│   │   │   ├── model_features.json
│   │   │   ├── model_metadata.json
│   │   │   └── respiratory_random_forest_v1.joblib
│   │   └── model_loader.py
│   │
│   ├── nlp/
│   │   ├── canonical_symptoms.json
│   │   ├── english_aliases.json
│   │   ├── feature_catalog.json
│   │   ├── swahili_aliases.json
│   │   └── symptom_mapper.py
│   │
│   ├── services/
│   │   ├── health_guidance.py
│   │   ├── history.py
│   │   ├── prediction.py
│   │   └── symptom_confirmation.py
│   │
│   └── main.py
│
├── tests/
│   ├── test_basic_endpoints.py
│   ├── test_history_feedback.py
│   ├── test_nlp_confirmation.py
│   └── test_prediction_validation.py
│
├── .env.example
├── .gitignore
├── requirements.txt
├── requirements-dev.txt
└── README.md