# Build / install for hzmetrics.
#
# Production install (run on the target HUBzero host):
#   sudo make install                       # install everything; cron form = dropin
#   sudo make install CRON_STYLE=spool      # use /var/spool/cron/apache instead
#   sudo make uninstall                     # remove what `install` put on the host
#
# Dev / CI:
#   make help                               # list targets
#   make lint                               # quick Python syntax check
#   make test                               # defensive A/B suite (no legacy/network)
#   make test-ab                            # full A/B suite (needs legacy + bind9-host + MariaDB)
#
# Honors DESTDIR for staged installs (packaging):
#   make install DESTDIR=/tmp/stage INSTALL_OWNER=root
#
# `install` does NOT chmod /etc/hubzero-metrics or drop access.cfg in
# place — those are operator-supplied secrets.  The install target
# prints the remaining manual steps at the end.

DESTDIR        ?=
PREFIX         ?= /opt/hubzero/bin
SYSCONFDIR     ?= /etc
CRONDDIR       ?= $(SYSCONFDIR)/cron.d
SPOOLCRONDIR   ?= /var/spool/cron
TMPFILESDIR    ?= $(SYSCONFDIR)/tmpfiles.d
INSTALL_OWNER  ?= apache
# `dropin` -> /etc/cron.d/hubzero-metrics ; `spool` -> /var/spool/cron/apache
CRON_STYLE     ?= dropin

SCRIPT         := hzmetrics.py
SCRIPT_DST     := $(DESTDIR)$(PREFIX)/hzmetrics.py
POSTROTATE_DST := $(DESTDIR)$(PREFIX)/hzmetrics-postrotate.sh
TMPFILES_DST   := $(DESTDIR)$(TMPFILESDIR)/hzmetrics.conf
CRONDROP_DST   := $(DESTDIR)$(CRONDDIR)/hubzero-metrics
CRONSPOOL_DST  := $(DESTDIR)$(SPOOLCRONDIR)/apache

.PHONY: help install uninstall test test-ab lint \
        install-script install-tmpfiles install-logrotate \
        install-cron-dropin install-cron-spool

help:  ## List all targets
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z][a-zA-Z0-9_-]*:.*##/ {printf "  %-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: install-script install-tmpfiles install-logrotate install-cron-$(CRON_STYLE)  ## Install everything (CRON_STYLE: dropin | spool)
	@echo
	@echo "Installed.  Remaining manual steps:"
	@echo "  sudo systemd-tmpfiles --create $(TMPFILESDIR)/hzmetrics.conf"
	@echo "  sudo install -d -o root -g apache -m 750 /etc/hubzero-metrics"
	@echo "  sudo install -o root -g apache -m 640 <your access.cfg> /etc/hubzero-metrics/access.cfg"
	@echo "  sudo -u apache python3 $(PREFIX)/hzmetrics.py setup-db        # first-time DB only"
	@echo "  sudo -u apache python3 $(PREFIX)/hzmetrics.py migrate --apply"

install-script:  ## Install hzmetrics.py to PREFIX
	install -D -o $(INSTALL_OWNER) -m 755 $(SCRIPT) $(SCRIPT_DST)

install-tmpfiles:  ## Install systemd-tmpfiles config
	install -D -m 644 conf/hzmetrics.tmpfiles.conf $(TMPFILES_DST)

install-logrotate:  ## Install logrotate postrotate hook script
	install -D -m 755 conf/hzmetrics-logrotate-postrotate.sh $(POSTROTATE_DST)

install-cron-dropin:  ## Install /etc/cron.d/hubzero-metrics form of the cron entry
	install -D -m 644 conf/hubzero-metrics.cron.d $(CRONDROP_DST)

install-cron-spool:  ## Install /var/spool/cron/apache form (apache user crontab)
	install -D -o $(INSTALL_OWNER) -m 600 conf/hubzero-metrics.cron.apache $(CRONSPOOL_DST)

uninstall:  ## Remove everything `install` puts on a host
	rm -f $(SCRIPT_DST) $(POSTROTATE_DST) $(TMPFILES_DST) $(CRONDROP_DST) $(CRONSPOOL_DST)

test:  ## Run the defensive A/B suite (no legacy required)
	./tests/ab/run-defensive.sh

test-ab:  ## Run the full A/B suite (requires tests/legacy + bind9-host + MariaDB)
	./tests/ab/run-all.sh

lint:  ## Quick Python syntax check
	python3 -m py_compile $(SCRIPT)
