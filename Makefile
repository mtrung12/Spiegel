# Campaign-Reaction / Spiegel
#
# make run       - build (if needed) and start the stack in Docker
# make rebuild   - rebuild the image from scratch and restart
# make dev       - run locally without Docker (npm run dev)
#
# Recipes are written for both cmd.exe (GnuWin32 make on Windows) and sh.

COMPOSE := docker compose
FRONTEND_URL := http://localhost:3000
BACKEND_URL := http://localhost:5001

.PHONY: help run rebuild dev stop down restart logs ps shell build setup clean

help:
	@echo Campaign-Reaction targets:
	@echo   make run       Build if needed and start the stack in Docker
	@echo   make rebuild   Rebuild the image with no cache and restart
	@echo   make dev       Run locally without Docker
	@echo   make logs      Follow container logs
	@echo   make stop      Stop the container, keep it around
	@echo   make down      Stop and remove the container
	@echo   make restart   down then run
	@echo   make ps        Show container status
	@echo   make shell     Open a shell inside the running container
	@echo   make setup     Install Node and Python deps on the host
	@echo   make clean     Remove the container and the local image

.DEFAULT_GOAL := help

run:
	$(COMPOSE) up -d --build
	@echo Frontend $(FRONTEND_URL)
	@echo Backend  $(BACKEND_URL)
	@echo Logs with: make logs

rebuild:
	$(COMPOSE) build --no-cache
	$(COMPOSE) up -d --force-recreate
	@echo Rebuilt. Frontend $(FRONTEND_URL)  Backend $(BACKEND_URL)

build:
	$(COMPOSE) build

stop:
	$(COMPOSE) stop

down:
	$(COMPOSE) down

restart: down run

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

shell:
	$(COMPOSE) exec spiegel bash

# Node deps are prerequisites, not part of the recipe: they install once, on the
# first run or after a wipe, instead of on every `make dev`. Backend deps need no
# rule - `uv run` syncs them itself.
dev: node_modules frontend/node_modules
	npm run dev

node_modules:
	npm install

frontend/node_modules:
	npm --prefix frontend install

setup:
	npm run setup:all

clean:
	$(COMPOSE) down --rmi local -v
