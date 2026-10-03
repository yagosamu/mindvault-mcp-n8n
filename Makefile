.DEFAULT_GOAL := help
.PHONY: help up down nuke ps logs n8n-logs qdrant-logs collection count open import list

# Recipes here use bash syntax. On Windows /bin/bash does not resolve unless
# make was launched from a POSIX shell, and the PATH's `bash` is the WSL stub
# (no distro). Point at Git for Windows' bash explicitly; PROGRA~1 is the 8.3
# short name for "Program Files", which avoids the space breaking the value.
ifeq ($(OS),Windows_NT)
SHELL := C:/PROGRA~1/Git/bin/bash.exe
else
SHELL := /bin/bash
endif

COMPOSE := docker compose

ifneq (,$(wildcard .env))
include .env
export
endif

QDRANT_PORT ?= $(or $(QDRANT_HOST_PORT),6353)
N8N_PORT    ?= $(or $(N8N_HOST_PORT),5678)
COLLECTION  ?= $(or $(QDRANT_COLLECTION),mindvault)

help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

# $(SHELL), not `bash`: this recipe has no shell metacharacters, so make would
# otherwise run it directly through CreateProcess and hit the WSL stub.
up: ## Start n8n + Qdrant and ensure the collection exists
	$(SHELL) scripts/bootstrap.sh

down: ## Stop containers (keep data)
	$(SHELL) scripts/bootstrap.sh --down

nuke: ## Stop AND wipe volumes (credentials + vectors are lost)
	$(SHELL) scripts/bootstrap.sh --nuke

ps: ## Show container status
	$(COMPOSE) ps

logs: ## Tail all logs
	$(COMPOSE) logs -f

n8n-logs: ## Tail n8n logs
	$(COMPOSE) logs -f n8n

qdrant-logs: ## Tail Qdrant logs
	$(COMPOSE) logs -f qdrant

import: ## Import workflows/*.json into n8n (re-run after editing them)
	@MSYS_NO_PATHCONV=1 $(COMPOSE) exec -T n8n n8n import:workflow --separate --input=/workflows

list: ## List the workflows n8n currently has
	@MSYS_NO_PATHCONV=1 $(COMPOSE) exec -T n8n n8n list:workflow

collection: ## Show the Qdrant collection config
	@curl -fsS "http://localhost:$(QDRANT_PORT)/collections/$(COLLECTION)" | python -m json.tool

count: ## Count indexed points in the collection
	@curl -fsS -X POST "http://localhost:$(QDRANT_PORT)/collections/$(COLLECTION)/points/count" \
		-H 'Content-Type: application/json' -d '{"exact":true}' | python -m json.tool

open: ## Print the URLs to open
	@echo "  n8n editor ....... http://localhost:$(N8N_PORT)"
	@echo "  Qdrant dashboard . http://localhost:$(QDRANT_PORT)/dashboard"
