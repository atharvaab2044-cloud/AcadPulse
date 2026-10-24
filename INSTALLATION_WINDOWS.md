# AcadPulse — Clean Windows Installation

This is the single canonical AcadPulse project. Use one extracted folder; do not apply phase-update ZIPs or copy source files from previous project folders.

## 1. Extract and open the correct folder

1. Download `AcadPulse_All_In_One.zip`.
2. Create a new empty folder, e.g. `C:\Users\HP\Documents\AcadPulse`.
3. Extract the ZIP contents into it.
4. In VS Code, select **File → Open Folder** and choose that folder.
5. Open **Terminal → New Terminal**. `Get-ChildItem` should show `run.py`, `requirements.txt`, `.env.example`, `app`, `tests`, and `docs`.

If `run.py` is not shown in the current directory, you are in the wrong folder. Use `Get-Location` and `Get-ChildItem` to confirm.

## 2. Check Python and make a virtual environment

Python 3.11 or newer is recommended. Python 3.13 should work when compatible wheels for dependencies are available.

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, allow it for this terminal only and retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

The prompt should start with `(.venv)`.

## 3. Install dependencies (one time)

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Core packages include Flask, Flask-SQLAlchemy, Flask-Migrate, Flask-Login, Flask-WTF, python-dotenv, PyMuPDF, openpyxl, ReportLab, PostgreSQL driver support and pytest. Local development uses SQLite; no MySQL/PostgreSQL server is required for a first run.

Optional table/data-processing dependencies:

```powershell
python -m pip install -r requirements-document-processing.txt
```

Optional packages do not implement OCR; scanned syllabus/result PDF processing still requires a later OCR implementation.

## 4. Create `.env` securely

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the generated key into `.env`, replacing the `SECRET_KEY` placeholder. Keep `.env` private and never commit it. Example settings (use your generated secret, not this placeholder):

```dotenv
APP_ENV=development
SECRET_KEY=paste_your_newly_generated_key_here
CURRENT_ACADEMIC_YEAR=2026-27
MAX_CONTENT_LENGTH=16777216
```

Leave `DATABASE_URL` unset to use the new isolated SQLite database. Do not point this project at the old application's DB.

## 5. Initialize the blank local database

Only on a brand-new, empty AcadPulse folder/database:

```powershell
flask --app run.py init-db
flask --app run.py create-admin
```

Follow the prompts to set the administrator email, name and password (minimum 12 characters). If you have already initialized the database and created the admin, do not repeat these commands.

## 6. Run the tests

```powershell
python -m pytest -q
```

You can alternatively double-click `TEST_ACADPULSE.bat` to run the same suite and keep the console visible. If test collection says Flask or SQLAlchemy is missing, confirm `(.venv)` is visible and run `python -m pip install -r requirements.txt` again. If tests fail, save the entire terminal output so the failures can be reproduced.

## 7. Start the app

```powershell
python run.py
```

Open `http://127.0.0.1:5000`. Keep the terminal open and press `Ctrl+C` to stop the server. After setup, you can also double-click `START_ACADPULSE.bat` to launch the app.

## 8. Daily startup (after installation)

Every new VS Code terminal:

```powershell
.\.venv\Scripts\Activate.ps1
python run.py
```

You do not reinstall dependencies or reinitialize the database each day.

## 9. Optional synthetic demo data

```powershell
flask --app run.py seed-demo
```

This command asks for confirmation and adds fictional records only. Do not use the demo data as actual student records.

## 10. Local file safety

The ZIP contains no `.env`, database, uploaded student/syllabus/result files, virtual environment, generated reports or backups. Local paths:

- `.env`: private configuration/secret
- `instance/`: local SQLite database
- `uploads/`: private source documents and import staging
- `reports/`: generated reports
- `backups/`: local backups

Do not upload real student records, passwords or `.env` to GitHub or chat.
