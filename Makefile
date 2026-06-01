# Build / install for hzmetrics.
#
# Production install (run on the target HUBzero host, as root):
#   sudo make install                       # deps + tree + scripts (idempotent)
#   sudo make uninstall                     # remove /opt/hubzero/metrics tree
#
# `install` is one root-only step.  Everything it does needs root —
# system-package install, `/opt` tree creation, chown to the service
# user — so the previous split into install-deps / install-bootstrap /
# install-script / install-cron-template / install-conf-sample was
# operator overhead without operator benefit.  Dirs are created or
# perm-corrected only when the service user can't reach them, so
# re-running `make install` is a no-op on a healthy tree (notably
# preserves `/var/log/hubzero/metrics → apache:access-logs` when that
# happens to be the host convention).
#
# `install` does NOT touch the conf/access.cfg secret or register the
# crontab — those are operator-supplied / operator-actioned.  The
# install target prints the remaining manual steps at the end.
#
# Dev / CI:
#   make help                               # list targets
#   make lint                               # quick Python syntax check
#   make test                               # defensive A/B suite (no legacy/network)
#   make test-ab                            # full A/B suite (needs legacy + bind9-host + MariaDB)
#
# Honors DESTDIR for staged installs (packaging):
#   make install DESTDIR=/tmp/stage

DESTDIR        ?=
HZMETRICS_HOME ?= /opt/hubzero/metrics
LOG_DIR        ?= /var/log/hubzero/metrics
INSTALL_OWNER  ?= apache
INSTALL_GROUP  ?= apache

SCRIPT             := hzmetrics.py
BIN_DST            := $(DESTDIR)$(HZMETRICS_HOME)/bin
CONF_DST           := $(DESTDIR)$(HZMETRICS_HOME)/conf
STATE_DST          := $(DESTDIR)$(HZMETRICS_HOME)/state
LOG_DST            := $(DESTDIR)$(LOG_DIR)
SCRIPT_DST         := $(BIN_DST)/hzmetrics.py
CRON_TEMPLATE_DST  := $(CONF_DST)/cron.apache
CONF_SAMPLE_DST    := $(CONF_DST)/hzmetrics.conf.sample

.PHONY: help install uninstall test test-ab lint

help:  ## List all targets
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z][a-zA-Z0-9_-]*:.*##/ {printf "  %-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## Install everything (deps + tree + scripts; run as root; idempotent)
	# --- Python deps (pymysql via dnf, aiodns via pip — no python3.11-aiodns RPM) ---
	dnf install -y python3.11-PyMySQL
	umask 022 && python3.11 -m pip install aiodns
	# --- /opt + /var/log tree (create if missing; only fix perms when the
	#     service user can't access — preserves existing groups like
	#     /var/log/hubzero/metrics → apache:access-logs that the host may
	#     already have configured) ---
	@for d in $(DESTDIR)$(HZMETRICS_HOME) $(BIN_DST) $(CONF_DST) $(STATE_DST) $(LOG_DST); do \
	    if [ ! -d "$$d" ]; then \
	        install -d -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 0750 "$$d"; \
	    elif ! sudo -n -u $(INSTALL_OWNER) test -r "$$d" -a -x "$$d" 2>/dev/null; then \
	        chown $(INSTALL_OWNER):$(INSTALL_GROUP) "$$d" && chmod 0750 "$$d"; \
	    fi; \
	done
	# --- Project-shipped files (always overwritten; these are upgrade-tracked,
	#     not operator-customized) ---
	install -D -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 755 $(SCRIPT) $(SCRIPT_DST)
	install -D -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 644 conf/hubzero-metrics.cron.apache $(CRON_TEMPLATE_DST)
	install -D -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 644 conf/hzmetrics.conf.sample $(CONF_SAMPLE_DST)
	@echo
	@echo "Installed under $(HZMETRICS_HOME)/.  Remaining manual steps:"
	@echo "  install -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 0600 <your access.cfg> $(HZMETRICS_HOME)/conf/access.cfg"
	@echo "  sudo -u $(INSTALL_OWNER) crontab $(HZMETRICS_HOME)/conf/cron.apache"
	@echo "  sudo -u $(INSTALL_OWNER) $(HZMETRICS_HOME)/bin/hzmetrics.py init"

uninstall:  ## Remove the install tree (leaves $(LOG_DIR) intact for postmortems)
	rm -rf $(DESTDIR)$(HZMETRICS_HOME)

test:  ## Run the defensive A/B suite (no legacy required)
	./tests/ab/run-defensive.sh

test-ab:  ## Run the full A/B suite (requires tests/legacy + bind9-host + MariaDB)
	./tests/ab/run-all.sh

lint:  ## Quick Python syntax check
	python3 -m py_compile $(SCRIPT)
