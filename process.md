# Translation Process 🕒

While the translation could be done manually, I use a special process to speed everything up.

I use AI for translation and proofreading. And while I am not a big fan of LLMs, they significantly speed up the process and save a lot of prescious time.

The process is as follows:

```mermaid
flowchart TB
subgraph Row1["Translation"]
direction TB
A["The translation file (.txt) is exported from the website"]
B["AI translates the file<br><b>Important</b>: feed it the Translation Guide for consistent results"]
C["AI-translated file (.txt) is imported back to the website"]
D["Human proofreads the translation and makes edits"]
E["The file is downloaded again and proofread by AI"]
F["Human makes final changes according to AI suggestions"]
G["Done!"]
A --> B --> C --> D --> E --> F --> G
end
```
