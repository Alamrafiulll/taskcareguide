# Short Invoice Insight Demo Script

**Target length:** about 90 seconds. Bracketed text is a screen action; do not read it aloud.

## Script

**[Show the app home page.]**

“This is Invoice Insight, a local document intelligence app for processing invoices. It accepts PDF, image, Excel, and CSV files. The API stores each upload, and a background worker processes it.”

**[Choose `test sample/01_valid_invoice.pdf` and `test sample/02_matching_invoice.xlsx`, then click Add selected invoices.]**

“I’m uploading a PDF invoice and its matching spreadsheet together. The upload queues each file, and the worker extracts the documents in the background.”

**[Show the documents list updating, then open one completed document.]**

“When processing completes, I can review the invoice number, date, supplier, customer, amounts, and line items. The app also shows validation findings and source evidence, so I can check where extracted values came from.”

**[Show the comparison section; select the PDF and spreadsheet and click Compare selected.]**

“I can compare two selected documents, including a PDF against a spreadsheet. The report shows matching, different, or missing fields. The app checks invoice identity before comparing, so unrelated files are not treated as a valid pair.”

**[Open a document flagged for review. Point to a field or finding. If configured, click Ask AI for suggestions and show the evidence.]**

“If a value needs review, I can correct it myself. Optional AI suggestions are proposals only: I review the supporting evidence, choose what to use, and save the correction. The app reruns validation and keeps a correction history.”

**[Show the download buttons.]**

“Finally, I can download the original file, a PDF summary of the currently saved invoice, or the JSON results. The original uploaded file remains unchanged.”

## Before recording

- Start the API, worker, and frontend using [the setup guide](setup-and-run.md); confirm the app shows the worker as ready.
- Use synthetic sample files only. For the comparison demo, upload `01_valid_invoice.pdf` with `02_matching_invoice.xlsx` and wait for both to complete.
- If showing AI, confirm the configured Ollama model is installed and running. Skip the AI portion if the suggestions button is disabled.
- Avoid claiming that corrections are automatic, that the app posts into an accounting system, or that local demo throughput represents thousands of concurrent documents.
