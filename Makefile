.PHONY: setup-venv install backend backend-prod frontend run run-prod firmware-compile

PYTHON ?= python3
VENV_DIR := backend/.venv
MPLCONFIGDIR ?= /tmp/matplotlib
ARDUINO_CLI ?= /home/kazuto/bin/arduino-cli
FQBN ?= esp32:esp32:esp32cam

setup-venv:
	$(PYTHON) -m venv $(VENV_DIR)
	. $(VENV_DIR)/bin/activate && pip install --upgrade pip
	. $(VENV_DIR)/bin/activate && pip install -r backend/requirements.txt

install: setup-venv

backend:
	. $(VENV_DIR)/bin/activate && cd backend && MPLCONFIGDIR=$(MPLCONFIGDIR) uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

backend-prod:
	. $(VENV_DIR)/bin/activate && cd backend && MPLCONFIGDIR=$(MPLCONFIGDIR) uvicorn app.main:app --host 0.0.0.0 --port 8000

frontend:
	cd frontend && $(PYTHON) -m http.server 5500

run:
	@set -e; \
	(. $(VENV_DIR)/bin/activate && cd backend && MPLCONFIGDIR=$(MPLCONFIGDIR) uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload) & BACKEND_PID=$$!; \
	(cd frontend && $(PYTHON) -m http.server 5500) & FRONTEND_PID=$$!; \
	trap 'kill $$BACKEND_PID $$FRONTEND_PID' INT TERM EXIT; \
	wait

run-prod:
	@set -e; \
	(. $(VENV_DIR)/bin/activate && cd backend && MPLCONFIGDIR=$(MPLCONFIGDIR) uvicorn app.main:app --host 0.0.0.0 --port 8000) & BACKEND_PID=$$!; \
	(cd frontend && $(PYTHON) -m http.server 5500) & FRONTEND_PID=$$!; \
	trap 'kill $$BACKEND_PID $$FRONTEND_PID' INT TERM EXIT; \
	wait

firmware-compile:
	cd esp32_cam/esp32_cam_stream && $(ARDUINO_CLI) compile --fqbn $(FQBN) .
