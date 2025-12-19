"""
Unified CLI for ScalpelLab
"""

import argparse
import sys
from pathlib import Path
from scalpellab.core.config import settings
from scalpellab.services.scanner import ScannerService
from scalpellab.services.conversion import ConversionService
from scalpellab.services.redaction import RedactionService

def handle_scan(args):
    """Handle the 'scan' command."""
    print(f"Scanning filesystem (DB: {settings.DB_PATH})...")
    scanner = ScannerService()
    
    seq_updates = {}
    if not args.skip_seq:
        print("Scanning SEQ files...")
        seq_updates = scanner.scan_seq()
    
    mp4_updates = {}
    if not args.skip_mp4:
        print("Scanning MP4 files...")
        mp4_updates = scanner.scan_mp4(calculate_duration=not args.fast)
    
    if args.dry_run:
        print(f"[DRY RUN] Would update {len(seq_updates)} SEQ and {len(mp4_updates)} MP4 entries.")
    else:
        scanner.sync_to_db(seq_updates, mp4_updates)
        print(f"Success: Updated {len(seq_updates)} SEQ and {len(mp4_updates)} MP4 entries.")

def handle_export(args):
    """Handle the 'export' command."""
    print(f"Exporting SEQ to MP4 (Target: {args.targets})...")
    # TODO: Implement target filtering logic
    converter = ConversionService()
    # Placeholder for batch logic
    print("Batch export not fully implemented in CLI yet. Use legacy scripts for now.")

def handle_redact(args):
    """Handle the 'redact' command."""
    print(f"Applying redaction (Config: {args.config})...")
    redactor = RedactionService()
    # Placeholder for batch logic
    print("Batch redaction not fully implemented in CLI yet. Use legacy scripts for now.")

def main():
    parser = argparse.ArgumentParser(description="ScalpelLab Unified CLI")
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # Scan
    scan_parser = subparsers.add_parser("scan", help="Update database from filesystem")
    scan_parser.add_argument("--dry-run", action="store_true", help="Preview changes")
    scan_parser.add_argument("--skip-seq", action="store_true", help="Skip SEQ scan")
    scan_parser.add_argument("--skip-mp4", action="store_true", help="Skip MP4 scan")
    scan_parser.add_argument("--fast", action="store_true", help="Skip duration calculation")

    # Export
    export_parser = subparsers.add_parser("export", help="Convert SEQ to MP4")
    export_parser.add_argument("--targets", type=str, default="all", help="Filtering criteria")

    # Redact
    redact_parser = subparsers.add_parser("redact", help="Apply redaction masks")
    redact_parser.add_argument("--config", type=str, default="times.xlsx", help="Path to Excel config")

    args = parser.parse_args()

    if args.command == "scan":
        handle_scan(args)
    elif args.command == "export":
        handle_export(args)
    elif args.command == "redact":
        handle_redact(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
