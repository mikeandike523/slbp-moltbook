
Technical Details:

Make sure you replace all recommended curl calls with
basic_web_request() tool with load_service_tokens:["moltbook"]
This will automatically load the moltbook token with requests, do not search on disk
for tokens.

Do not touch filesystem. Everything you need is in tool calls and session memory.
This includes not writing to

Whenever the moltbook guidelines mention memory, use session memory tools instead
of files on disk.

Whenever the guidelines mention updating the skill instructions, do not write files to disk
or run curl. Rather, repeat the load_skill_files_from_url_to_session_memory tool above with the same arguments as before.

Almost every action on moltbook has a verification challenge. Look at the returned
JSON from any operation you do on moltbook api. If it has verification challenge fields,
follow the instructions in the return json. This will likely involve an additional
basic_web_request tool call.