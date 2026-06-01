# Build / install for hzmetrics.
#
# Production install (run on the target HUBzero host):
#   sudo make install                       # install under /opt/hubzero/metrics (root required to set ownership)
#   sudo make uninstall                     # remove /opt/hubzero/metrics tree
#
# Dev / CI:
#   make help                               # list targets
#   make lint                               # quick Python syntax check
#   make test                               # defensive A/B suite (no legacy/network)
#   make test-ab                            # full A/B suite (needs legacy + bind9-host + MariaDB)
#
# Honors DESTDIR for staged installs (packaging):
#   make install DESTDIR=/tmp/stage
#
# `install` does NOT touch the conf/access.cfg secret or register the
# crontab — those are operator-supplied / operator-actioned.  The
# install target prints the remaining manual steps at the end.

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

.PHONY: help install install-bootstrap install-deps uninstall test test-ab lint \
        install-script install-cron-template install-conf-sample

help:  ## List all targets
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z][a-zA-Z0-9_-]*:.*##/ {printf "  %-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install-deps:  ## Install Python dependencies (pymysql via dnf, aiodns via pip; run as root)
	# pymysql ships as an RPM (distro-signed, version-pinned, no perms quirk).
	dnf install -y python3.11-PyMySQL
	# aiodns has no python3.11-* RPM in any reachable repo, so pip is the
	# only option for it.  umask 022 keeps the installed files world-readable
	# so apache can import them; check that /usr/local/lib/python3.11/{,site-packages}
	# is mode 0755 (RHEL's pip can leave it 0700 root, which hides the package
	# from non-root site.py — see deployment.md).
	umask 022 && python3.11 -m pip install aiodns

install-bootstrap:  ## One-time root step: create HZMETRICS_HOME owned by INSTALL_OWNER
	install -d -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 0750 \
	    $(DESTDIR)$(HZMETRICS_HOME) \
	    $(BIN_DST) $(CONF_DST) $(STATE_DST) $(LOG_DST)

install: install-script install-cron-template install-conf-sample  ## Install everything (run as INSTALL_OWNER once bootstrap is done)
	@echo
	@echo "Installed under $(HZMETRICS_HOME)/.  Remaining manual steps:"
	@echo "  # if first install, run the one-time root bootstrap once per host:"
	@echo "  #   sudo make install-bootstrap"
	@echo "  install -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 0600 <your access.cfg> $(HZMETRICS_HOME)/conf/access.cfg"
	@echo "  sudo -u $(INSTALL_OWNER) crontab $(HZMETRICS_HOME)/conf/cron.apache"
	@echo "  sudo -u $(INSTALL_OWNER) $(HZMETRICS_HOME)/bin/hzmetrics.py setup-db        # first-time DB only"
	@echo "  sudo -u $(INSTALL_OWNER) $(HZMETRICS_HOME)/bin/hzmetrics.py migrate --apply"

install-script:  ## Install hzmetrics.py to $(HZMETRICS_HOME)/bin/
	install -D -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 755 $(SCRIPT) $(SCRIPT_DST)

install-cron-template:  ## Install the cron template (operator registers it via `crontab`)
	install -D -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 644 \
	    conf/hubzero-metrics.cron.apache $(CRON_TEMPLATE_DST)

install-conf-sample:  ## Install the hzmetrics.conf.sample reference
	install -D -o $(INSTALL_OWNER) -g $(INSTALL_GROUP) -m 644 \
	    conf/hzmetrics.conf.sample $(CONF_SAMPLE_DST)

uninstall:  ## Remove the install tree (leaves $(LOG_DIR) intact for postmortems)
	rm -rf $(DESTDIR)$(HZMETRICS_HOME)

test:  ## Run the defensive A/B suite (no legacy required)
	./tests/ab/run-defensive.sh

test-ab:  ## Run the full A/B suite (requires tests/legacy + bind9-host + MariaDB)
	./tests/ab/run-all.sh

lint:  ## Quick Python syntax check
	python3 -m py_compile $(SCRIPT)
