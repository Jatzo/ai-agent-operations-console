# Running Claude Code read only from a web page, and how I tested it

I wanted a page on my own computer where I could click a project and ask Claude Code about it. Claude Code is
built to work in a terminal that you watch. A web page starts it, walks away, and has no one to approve anything.
So several things have to be true before it is safe to leave running. Below are the ones I made true, how I checked
them, and what I have not checked.

## What can go wrong

Four things, in the order I worried about them.

1. **The page.** Anything the browser sends could try to become a folder, a flag or a command.
2. **A file Claude reads.** Text in a file can tell Claude to do something. Whatever Claude can reach, a file can
   try to steer it to.
3. **The command changing.** A new Claude Code version can change what a flag means, and the safety checks were
   written for the old one.
4. **A run that does not end.** A stuck process keeps costing money after the person has closed the tab.

## The controls

| Control | Where | Test that fails if it breaks |
|---|---|---|
| The page sends a message, a node id and a chat id. Never a path, a flag or a command. | `serve_dashboard.py`, `chat.py` | `test_the_page_cannot_supply_a_folder_a_flag_or_a_command` |
| Three tools only (Read, Glob, Grep), safe mode, restricted mode, no MCP servers, permissions pinned to `dontAsk`, $1 a message. | `chat.build_command` | `test_every_safety_flag_is_present`, `test_only_the_three_read_only_tools_are_offered` |
| Secret file names denied for all three tools: `.env*`, `*.pem`, `*.key`, `id_*`, `*credentials*`, `*secret*`, `*token*` and more. | `chat.SECRET_PATTERNS` | `test_each_secret_pattern_is_denied_for_each_file_tool` |
| Extra folders only from a short list, resolved through junctions. The Claude folder, its transcripts and its settings are always refused, wherever `--home` points. | `chat.extra_folder`, `private_folder` | the refusal tests in `test_chat_context.py` and `test_portable.py` |
| Only seven kinds of event reach the page. Everything else, and every line of Claude's error output, is dropped. | `chat.stream` | `test_unknown_events_garbage_lines_and_init_data_never_reach_the_page`, `test_a_sign_in_failure_gives_the_fixed_message_and_never_the_raw_stderr` |
| One message at a time, 5 minutes in total, 90 seconds of silence, $5 a session. A killed run is charged $1. | `ChatManager` | the limit tests in `test_chat_runner.py` |
| Stop, a dropped connection and closing the server all kill the process and anything it started. | `kill_tree`, `JobObject` | `test_a_process_that_started_its_own_child_loses_the_child_too`, `test_a_dropped_connection_is_noticed_on_a_heartbeat_even_when_nothing_is_being_said` |
| Chat refuses to start until `tests/chat_gate.py` has passed for the installed Claude Code version. | `ChatManager.status` | `test_chat_is_refused_when_the_version_has_not_been_tested` |
| Localhost only, a token on every call, host and origin checks, no outside addresses in the page. | `serve_dashboard.py` | `test_every_chat_endpoint_needs_the_token` and the other endpoint tests |
| The server writes nothing but one inbox line. | everywhere | `test_after_a_chat_with_every_kind_of_node_nothing_on_disk_has_changed` |

## What the real command showed

The tests above use a fake Claude. The fake can only prove what I thought of, so `tests/chat_gate.py` runs the
real command in a throwaway folder with decoy secrets. It has 22 checks and costs about $0.06.

It found things the fake could not:

- **My settings changed the permission mode.** The real command reported `auto`, taken from my own settings, not the
  mode I had assumed. All three modes refused a file outside the folder, but I now pin `dontAsk` so the result does
  not depend on anyone's settings.
- **Safe mode is not empty.** It still lists the built-in plugins and bundled skills. Your own skills, installed
  plugins and MCP servers are not loaded, and the tool list is exactly three.
- **A review of the design worried that added folders and project settings might widen access.** The gate now checks both. A
  secret file in an added folder was not read, and an allow rule and a hook planted in the project settings did
  nothing.

The gate checks come in two kinds. For Grep and Glob it uses a control: a visible file must appear, and a secret
file next to it must not. That shows the denial, not a model that happened to decline. For "create a file" and
"run a command" it asks the model and then checks the folder, which cannot tell a blocked tool from a polite model.
There the evidence is the tool list.

## How I tested the tests

A test that cannot fail proves nothing. `tools/mutation_check.py` takes 54 changes to the code, each
removing one protection (drop `--safe-mode`, stop pinning the permission mode, skip the idle limit, forget to clean a
file name), applies one at a time to a temporary copy, and checks that the named test fails. A crash on import does
not count.

Five of them slipped past the tests at first. Each got a new test:

- Killing the process tree has two layers, `taskkill` and a Windows job object. Removing one changed nothing, so
  the test only fails when both go. The mutation now removes both.
- A dropped connection was only noticed when Claude was talking. A silent run kept going.
- Two clean-up functions, one for status lines and one for report names, were tested on their own but never on the
  path where they are used.
- The check that refuses the Claude folder was hidden behind a second check that refused the same paths. The test
  now needs a case where only the first check stands in the way.

Run it yourself: `python tools\mutation_check.py`. The last full run printed `54 of 54 mutations caught.` and took about 25 minutes.

## What this does not prove

- **One Claude Code version, one machine, Windows.** The gate records the version it passed. It does not tell you
  about the next one, which is why chat switches itself off after an update.
- **The secret list matches file names.** A key inside `config.json` is readable.
- **A file can still steer Claude.** Read only limits the damage, and what Claude reads is sent to Anthropic. A
  hostile file can still choose which other allowed files get read and sent.
- **Resuming a chat is tested only against the fake.** The real command is exercised one message at a time.
- **The kill code is Windows specific.** Other systems are untested.

## If you try it

Run `python demo.py` first. It costs nothing. Before you point it at a real folder, read the list above, then run
the gate and read what it printed.
