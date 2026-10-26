LIMITLESS MECHANICAL | OPERATIONS HUB v1.5
Local development build — not publicly hosted

TO START ON A DESKTOP (Python 3.11+):
1. Extract the archive to a private local folder.
2. In the extracted limitless-crm folder run:
   pip install -r requirements.txt
3. Create an owner account:
   python -m server.create_user --username owner --role owner --name "Office Owner" --po-prefix DR
4. Run:
   uvicorn server.main:app --host 127.0.0.1 --port 8000
5. Open http://127.0.0.1:8000/ and sign in.
6. Add technician users with their individual login credentials.
   The technician portal is at /tech; the field form is linked from assigned jobs.

IMPORTANT:
- No employee passwords or test-user accounts are included.
- A clean, point-in-time import of the LMS Job Board is shipped in the SQLite database.
- The old Google Sheet is not automatically synchronized; keep it active until final cutover.
- Purchase orders remain one-per-job with buyer prefixes DR / AR / DB. No manager approval.
- Sensitive customer and financial data is present: keep this ZIP PRIVATE.
- Customer signature is for service acknowledgment, not independently verified identity.
- Do not expose this development server to the public internet or rely on it for live billing.
- For full new features, API endpoints, limitations and tests, see README.md.

Equipment History: technicians open a work order /field form, select an asset, and tap View Equipment History; office staff use /equipment and select View Equipment History.

NEW v1.5 OFFICE PAGES:
- /estimates-workspace  : create/revise PDF proposals, options and record verified customer acceptance
- /pm                   : maintenance contracts, covered assets, scheduling, repair quote conversion
- Recurring PM command: python -m server.schedule_maintenance --days-ahead 7
- This command requires a trusted scheduler after cloud hosting; it is not running automatically in local dev.
