# Summary Instructions

## Task

- Generate a summary from the provided transcript and metadata.
- Fill the selected output template.
- Do not include these instructions, template comments, the template itself, or the full transcript in the final summary.

## Input Contract

- The transcript contains a `metadata` section and a `transcript text` section.
- Metadata keys match template placeholders when available.
- Use metadata values to fill matching placeholders.

## Scope

- Use only the provided metadata and transcript.
- Do not add outside knowledge.
- Do not infer visual information that is not stated in the transcript.
- This workflow is intended for spoken videos where audio carries most of the information.
- If the transcript is unsuitable for audio-only summarization, state the limitation in the required limitations section.

## Language

- Write the summary primarily in the template language.
- Preserve non-template-language terms or expressions when they appear in the transcript and are meaningful.

## Template Rules

- Replace all placeholders.
- Do not leave `{{...}}` placeholders in the final summary.
- Keep sections that are not marked as optional.
- Optional sections may be removed when they are not useful.
- If an optional section is removed, remove its heading as well.
- Read and write all text files as UTF-8.

## Timestamp Rules

- Use timestamps only from the transcript text.
- Keep timestamp format as `HH:MM:SS` or `HH:MM:SS - HH:MM:SS`.
- Attach timestamps to important points when possible.
- Do not invent timestamps.

## Safety Against Hallucination

- If information is missing, write that it is not available.
- If STT appears wrong or uncertain, mark the affected content as uncertain.
- Do not overstate conclusions beyond what the transcript supports.
