ITP MARKET INTELLIGENCE 3.1.0 HOTFIX

1. Stop the application with STOP.bat.
2. Extract the hotfix into the current project root and replace files.
3. Run APPLY_3_1_0_HOTFIX.bat.
4. Run SELF_TEST_MVP.bat.
5. Start the application with START.bat.
6. Open Products and run:
   Kaspi -> Exact seller offers -> Whole catalogue.

The migration creates a database backup before changing the schema.
Old candidate matches are preserved but excluded from current analytics.
The hotfix does not contain config.json or any database file.
