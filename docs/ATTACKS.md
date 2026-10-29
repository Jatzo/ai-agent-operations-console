# Try to break it

Each row is an attack, written as a test, against the console. It names what stops the attack, the test that fails if
that stops working, and the deliberate breakage that `tools/mutation_check.py` confirms the test notices. Where the
last column says "none yet", no breakage covers that control, so the test is the only evidence. The checker runs the
test files listed for each breakage in `tools/mutations.json`, so a breakage is noticed by those files, which is not
always the single test named in the row. Run any test by name: `python -m pytest tests -k <name>`.

| What is tried | What stops it | The test that fails if it stops | The breakage the checker confirms it notices |
|---|---|---|---|
| A page on another web site posts to the server | Origin check, JSON-only bodies | `test_a_message_from_another_origin_is_refused`, `test_a_message_that_is_not_json_content_is_refused` | none yet |
| A request with a forged or missing Host header (DNS rebinding) | Host check on every call | `test_a_wrong_or_missing_host_is_refused_on_get_and_post`, `test_every_chat_endpoint_needs_this_computers_host_name` | none yet |
| A call with no token, or the token from an earlier run | A new token each run, checked on every API call | `test_every_api_get_needs_the_token`, `test_the_token_is_different_on_every_run_and_an_old_token_is_refused` | none yet |
| A path, or a file name, asked for in place of the three page files | A fixed file table | `test_only_the_three_page_files_are_served` | none yet |
| A folder, a flag or a command sent as part of a chat message | The page sends only a message, a node id and a chat id, and the server looks the node up itself | `test_the_page_cannot_supply_a_folder_a_flag_or_a_command`, `test_a_project_path_that_is_not_in_the_index_is_never_used_as_a_folder` | `project path trusted from the page`, `node taken from the page without checking the world` |
| A chat id or a session id that is not one the server made | Format checks before anything starts | `test_a_chat_id_that_is_not_one_of_ours_is_refused`, `test_a_session_id_that_is_not_a_uuid_is_refused` | `chat id not checked`, `session id not checked` |
| A body that is too big, has no length, or is sent in chunks | Content-Length required, size limits | `test_a_body_over_24_kb_is_refused_and_one_just_under_is_read`, `test_a_message_with_no_length_or_with_chunking_is_refused` | none yet |
| Text with a newline or invisible characters, to forge an inbox line | The idea is cleaned, refused if it cannot be, and written as one line | `test_hostile_text_is_stored_literally_and_returned_as_plain_data`, `test_a_request_with_one_fault_is_refused_and_the_inbox_is_unchanged` | `hidden character list emptied` |
| Hostile text in a file name or a report turning into page code | Text is set with `textContent` only, no inline script, no outside addresses | `test_the_served_files_have_no_innerhtml_no_comments_and_no_outside_addresses` | none yet |
| A hostile name reaching Claude's prompt | Only validated names and paths are put in the prompt | `test_the_prefix_uses_only_validated_names_and_paths` | `hostile name reaches the prompt` |
| A junction that leads out of the folders the chat may share | Paths are resolved and checked against a short list | `test_a_junction_to_a_folder_outside_the_allowed_places_is_refused_and_one_to_an_allowed_place_is_resolved` | `links not resolved`, `allowed places not checked` |
| The private Claude folder (login, settings, transcripts) used as the index folder | The console refuses to start with it, and the chat refuses if asked | `test_starting_with_the_private_claude_folder_as_the_index_is_refused`, `test_the_chat_will_not_start_when_the_index_folder_is_the_private_claude_folder` | `start allowed with the private Claude folder as the index`, `chat allowed with the private Claude folder as the index` |
| A repo whose own git config names a program to run | Git is read with `core.fsmonitor` and `log.showSignature` off | `test_reading_a_repo_never_runs_the_program_its_own_config_names` | `git runs the program a repo's config names` |
| A request to widen the tools or turn off the safety flags | Three tools only, and safe and restricted mode always on | `test_only_the_three_read_only_tools_are_offered`, `test_safe_mode_and_restricted_are_both_on_and_the_flags_that_widen_access_never_appear` | `tools widened to Bash`, `safe mode dropped` |
| Claude asked to read a secret file | Secret file names are denied for Read, Grep and Glob. Pytest proves the flags are sent. Only the paid real-Claude gate proves Claude Code obeys them | `test_each_secret_pattern_is_denied_for_each_file_tool` | `Grep denials dropped`, `Glob denials dropped`, `env file pattern dropped`, `credentials pattern dropped` |
| Claude's own error text carrying a secret to the page | Every error shown is a fixed message | `test_a_sign_in_failure_gives_the_fixed_message_and_never_the_raw_stderr` | `raw error text forwarded` |
| A process that starts its own child, then hangs or is abandoned | The whole tree is killed on stop, a dropped connection, silence or the time limit | `test_a_process_that_started_its_own_child_loses_the_child_too`, `test_a_dropped_connection_is_noticed_on_a_heartbeat_even_when_nothing_is_being_said`, `test_a_silent_process_is_killed_after_the_idle_limit_and_counts_as_a_dollar` | `tree kill and job object off`, `dropped connection ignored on a heartbeat`, `idle limit off` |
| A run that does paid work and then fails, to get round the spending cap | A run that worked and ended without a result still counts as $1 | `test_a_run_that_did_work_and_then_ended_without_a_result_still_counts_towards_the_cap` | `a run that worked and then failed is not charged` |

## What was not tried

- **Another program on the same computer.** The token keeps out other web sites. Any program, or any other Windows
  account on the same machine, can fetch the page and read the token from it.
- **Another system.** Everything here was run on Windows 11 with Python 3.13 and one version of Claude Code. The process
  kill is Windows specific.
- **A resumed chat against the real command.** It is tested only against the fake.
- **A key inside a file with an ordinary name.** The secret list matches file names, so a key in `config.json` is readable.

The limits are explained further in [the write-up](WRITEUP.md).
