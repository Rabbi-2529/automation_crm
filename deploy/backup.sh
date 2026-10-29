#!/bin/sh
# ThingBD CRM backups. Runs in the `backup` service (postgres:16-alpine image, so pg_dump/pg_restore match the server).
#
#   sh /backup.sh loop                  # service default: back up every night at BACKUP_HOUR (and now, if none today)
#   sh /backup.sh once                  # back up right now
#   sh /backup.sh list                  # show backups
#   sh /backup.sh restore FILE --yes    # replace the database with FILE (stop web/worker/beat first)
#
# Each run writes /backups/crm-YYYYmmdd-HHMMSS.dump (pg_dump custom format) and media-YYYYmmdd-HHMMSS.tgz,
# then deletes backups older than BACKUP_KEEP_DAYS. Connection settings come from PGHOST/PGUSER/PGPASSWORD/PGDATABASE.
set -eu

DIR=/backups
KEEP="${BACKUP_KEEP_DAYS:-14}"
HOUR="${BACKUP_HOUR:-2}"

log() { echo "[backup $(date '+%Y-%m-%d %H:%M:%S')] $*"; }

run_once() {
	stamp=$(date +%Y%m%d-%H%M%S)
	mkdir -p "$DIR"
	pg_dump --format=custom --no-owner --no-privileges --file="$DIR/.crm-$stamp.partial"
	mv "$DIR/.crm-$stamp.partial" "$DIR/crm-$stamp.dump"
	if [ -d /media ] && [ -n "$(ls -A /media 2>/dev/null)" ]; then
		tar -czf "$DIR/.media-$stamp.partial" -C /media .
		mv "$DIR/.media-$stamp.partial" "$DIR/media-$stamp.tgz"
	fi
	find "$DIR" -maxdepth 1 \( -name 'crm-*.dump' -o -name 'media-*.tgz' \) -mtime +"$KEEP" -exec rm -f {} \;
	find "$DIR" -maxdepth 1 -name '.*.partial' -mmin +120 -exec rm -f {} \;
	date +%s > "$DIR/last_success"
	log "ok: crm-$stamp.dump ($(du -h "$DIR/crm-$stamp.dump" | cut -f1)), keeping $KEEP days"
}

seconds_until_next_run() {
	now=$(( $(date +%H | sed 's/^0//') * 3600 + $(date +%M | sed 's/^0//') * 60 + $(date +%S | sed 's/^0//') ))
	wait=$(( HOUR * 3600 - now ))
	[ "$wait" -le 0 ] && wait=$(( wait + 86400 ))
	echo "$wait"
}

case "${1:-once}" in
	once)
		run_once
		;;
	loop)
		log "nightly backups at $HOUR:00 ($(date +%Z)), keeping $KEEP days"
		last=$(cat "$DIR/last_success" 2>/dev/null || echo 0)
		if [ $(( $(date +%s) - last )) -gt 86400 ]; then
			run_once || log "FAILED (will retry at the next scheduled run)"
		fi
		while true; do
			sleep "$(seconds_until_next_run)"
			run_once || log "FAILED (will retry at the next scheduled run)"
			sleep 60
		done
		;;
	list)
		ls -lh "$DIR" | grep -E 'crm-|media-' || echo "No backups yet."
		;;
	restore)
		file="${2:-}"
		[ -n "$file" ] || { echo "Usage: sh /backup.sh restore crm-YYYYmmdd-HHMMSS.dump --yes"; exit 2; }
		case "$file" in /*) ;; *) file="$DIR/$file" ;; esac
		[ -f "$file" ] || { echo "No such backup: $file"; exit 2; }
		[ "${3:-}" = "--yes" ] || { echo "This REPLACES database $PGDATABASE with $file. Stop web, worker and beat, then re-run with --yes."; exit 2; }
		log "restoring $file into $PGDATABASE"
		pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction --dbname="$PGDATABASE" "$file"
		log "restore finished. Start web, worker and beat again (migrate runs first)."
		;;
	*)
		echo "Unknown command: $1 (use once, loop, list or restore)"; exit 2
		;;
esac
