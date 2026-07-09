#!/bin/bash
# Centralized ticket storage paths. Sourced by all ticket scripts.
# Agents should use ticket scripts, not access files directly.

TICKET_STORE="${COMPANY_DIR:-.company}/departments"

# Get ticket directory for a department
# Usage: ticket_dir DEPT
ticket_dir() {
  echo "${TICKET_STORE}/${1}/tickets"
}
