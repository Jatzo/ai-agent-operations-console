# Pointing the console at your own folders

The console reads one small folder of its own, called the index folder. It holds two files:

- `CLAUDE.md`, with two tables: where your projects are, and where your routines leave their reports.
- `INBOX.md`, where loose ideas go. The page adds one line to it when you add an idea.

You can write both by hand. If you would rather have Claude Code write them, open Claude Code in a new, empty
folder (this becomes your index folder) and paste the prompt below. It asks you before it writes anything, and it
only creates those two files.

## The prompt

```text
I want to point a local page called AI agent operations console at my own work. It needs two files in this folder: CLAUDE.md and INBOX.md. Create nothing else, and do not change, move or delete any file outside this folder.

First, ask me for the full path of each project folder I want shown, one at a time, and what to call it. Check that each path exists and is a folder. Tell me if one does not.

Then ask me whether any of my routines or scripts leave a report in a folder or in one fixed file. For each, ask for what it is called, when it runs, and the path. If the report file has a date in its name, ask me to show one example, and write the date part as YYYY-MM-DD.

Show me both files in full and wait for me to say yes before you write them.

CLAUDE.md must follow this layout exactly. The two headings are read by the page, so do not rename them. Each table's last column must hold the path in backticks. Use full paths.

# Index

## Where things are

| What | Where |
|---|---|
| <name I gave> | `<full path>` |

## Where each routine leaves its report

| Routine | When | Report |
|---|---|---|
| <name> | <when> | `<full path, with YYYY-MM-DD in place of the date if it has one>` |

INBOX.md must be exactly:

# Inbox

One line per idea.

When both files are written, tell me the folder you are in and give me this command to run in PowerShell, with the folder filled in:

python serve_dashboard.py --root "<this folder>"
```

## Check it

Run the command the prompt gives you from the project folder. If the index is wrong, the server stops and says
which part it could not read. The Cards tab also shows any report location it could not find, so a typo in a path
shows up there.

## Rules the page follows

- Projects are folders. A row whose path is a file is skipped, and so is a folder called `To Build`.
- A report path with `YYYY-MM-DD` in it means "any file in that folder that fits the pattern". A report path
  without it means one fixed file.
- The page reads folders and git history and never changes them. The one thing it writes is a line added to
  `INBOX.md` when you add an idea.
