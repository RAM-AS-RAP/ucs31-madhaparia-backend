# Respiratory Health Awareness Backend

Backend API for the IS Project 2 Respiratory Health Awareness and Prediction System.

The backend is being developed with FastAPI and will connect the NLP symptom-mapping component, trained Random Forest model, database and frontend application.

The system is intended for health-awareness support and does not provide a medical diagnosis.

## Current Backend Foundation

The initial backend currently provides:

- FastAPI application setup
- Environment-based configuration
- Root API endpoint
- Health-check endpoint
- Automatic Swagger API documentation
- Python virtual environment support
- Git configuration for development files

## Project Structure

```text
ucs31-madhaparia-backend/
│
├── app/
│   ├── __init__.py
│   ├── main.py
│   └── core/
│       ├── __init__.py
│       └── config.py
│
├── tests/
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt
```

## Environment Setup

Create a virtual environment:

```powershell
python -m venv .venv
```

Activate the virtual environment in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the backend dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Environment Configuration

The example environment configuration is stored in:

```text
.env.example
```

Current configuration:

```env
APP_NAME=Respiratory Health Awareness API
APP_ENV=development
```

A local `.env` file can be used for development configuration and is excluded from Git.

## Running the Backend

Start the FastAPI development server with:

```powershell
python -m uvicorn app.main:app --reload
```

The backend will be available at:

```text
http://127.0.0.1:8000
```

## Current API Endpoints

### Root

```text
GET /
```

Example response:

```json
{
  "message": "Respiratory Health Awareness API is running."
}
```

### Health Check

```text
GET /health
```

Example response:

```json
{
  "status": "ok",
  "service": "Respiratory Health Awareness API",
  "environment": "development"
}
```

## API Documentation

FastAPI automatically provides interactive Swagger documentation at:

```text
http://127.0.0.1:8000/docs
```

## Planned Backend Development

Future backend development will include:

- Random Forest model integration
- NLP symptom mapping
- English and Swahili symptom processing
- Symptom confirmation
- Red-flag safety checks
- Top 3 prediction API
- Health-awareness guidance
- Database integration
- Prediction history
- User feedback
- Validation and automated testing

## Disclaimer

This application is intended for educational and health-awareness purposes only.

Its output must not be treated as a medical diagnosis, prescription or replacement for professional healthcare.