# Translation Process 🕒

While the whole translation could be done manually, I use AI to help with the process. It reduces the amount of manual labor, speeds up the translation and improves consistency across files.

Below you can find the description of the processes.

> **⚠ Note:** Personally, I'm not a big fan of large language models (LLMs), because they raise serious ethical concerns: the electricity and water they consume, the forests cut for placing data centers, and the content scraped without its creators' consent, among others.
> 
> Even so, I ultimately decided that using AI for this translation is worth it. It dramatically cuts the time the work takes and it improves consistency, because the AI can cross-reference the translation guide and glossary while proofreading in a way that is hard to match by hand.

<details>

<summary>Current Process</summary>

- I use the **Hermes AI Agent** connected to a powerful model **DeepSeek V4.1-Flash**.
- Translation files live directly in the repository.

1. User tells model what to do (e.g.: "Translate TPC20).
2. Model chooses the right skill for the task. Currently, 4 skills are used: *brok-ua-global-edits*, *brok-ua-proofread*, *brok-ua-translate* and *brok-ua-workflow*.
3. Model translates the file, referencing the translation guide and glossary for consistent results. It can also fetch the translation in other languages from the BROK translation website directly.
4. Model makes the changes and recommends updates to the glossary, leading to continuous improvement.
5. User proof-reads the changed git lines and makes edits if needed.
6. User commits the changes on GitHub, exports the folder as ZIP and uploads the file to the translation website.
7. Done!

</details>

<details>

<summary>Old Process</summary>

- I use **Gemini 3.6/3.7/3.8-Flash** for the task.
- Translation files have to be downloaded manually.

1. User downloads the file (.txt) from the website.
2. Model translates the file, given a special system prompt.
3. User imports the AI-translated file (.txt) back onto the website.
4. User proof-reads the translation and edits in their own judgement.
5. User downloads the file again.
6. Model proof-reads the file, given a special system prompt.
7. User makes any final changes according to AI suggestions.
8. Done!

</details>