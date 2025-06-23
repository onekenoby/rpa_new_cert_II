"""
wath the current bot does:

============================================================================================

+-------------------------------------------------------------+
|                 RobotSpareBin Order Workflow                |
+-------------------------------------------------------------+
        |
        v
+------------------+
|   Start Robot    |
+------------------+
        |
        v
+------------------+
| Download orders  |-- CSV -->[orders.csv]
|  CSV from URL    |
+------------------+
        |
        v
+------------------+
| Launch browser & |
|  open shop URL   |
+------------------+
        |
        v
+---------------------------+
| Dismiss “OK” modal if any |
+---------------------------+
        |
        v
+-------------------------------------------------------------+
|                  **FOR EACH ORDER ROW**                     |
+-------------------------------------------------------------+
        |
        v
+--------------------------+
| Fill web-form fields:   |
|  • Head (select)        |
|  • Body (radio)         |
|  • Legs (#)             |
|  • Address (text)       |
+--------------------------+
        |
        v
+--------------------------+
| Click [Order] button     |
+--------------------------+
        |
        v
+--------------------------+
| Is receipt visible?      |
+----------+---------------+
           |Yes                      |No
           v                         v
   +--------------------+    +--------------------+
   | Continue workflow  |    | Retry (max 3)      |
   +--------------------+    +--------------------+
           |                         |
           +-----------<-------------+
                        (fail hard after 3)
           |
           v
+--------------------------+
| Create PDF from receipt |
+--------------------------+
        |
        v
+--------------------------+
| Screenshot robot preview |
+--------------------------+
        |
        v
+------------------------------+
| Embed screenshot in the PDF |
+------------------------------+
        |
        v
+--------------------------+
|   Click [Order another]  |
+--------------------------+
        |
        v
+--------------------------+
| Dismiss modal (if shown) |
+--------------------------+
        |
        v
+-------------------------------------------------------------+
|                 **END FOR-EACH LOOP**                       |
+-------------------------------------------------------------+
        |
        v
+--------------------------+
|   Zip all PDF receipts   |
|   → robot_orders.zip     |
+--------------------------+
        |
        v
+------------------+
|     Finish       |
+------------------+

============================================================================================

...in few words:
• Downloads the CSV of orders
• Opens the web shop and iterates through every order
• Saves the HTML receipt as a PDF
• Takes a screenshot of the rendered robot
• Embeds the screenshot in the PDF
• Zips all PDFs into output/robot_orders.zip

Everything is stored inside the `output/` directory so Control Room
will automatically collect the artefacts.

Author: by Stefano Ciotti
"""
from RPA.Assistant import Assistant as Assistant
from pathlib import Path
from robocorp.tasks import task
from robocorp import browser
from RPA.HTTP import HTTP
from RPA.Tables import Tables
from RPA.PDF import PDF
from RPA.FileSystem import FileSystem
from RPA.Archive import Archive



#--------------------------------------------------------------------------- #
# Constants & paths
# --------------------------------------------------------------------------- #

URL_SHOP   = "https://robotsparebinindustries.com/#/robot-order"
URL_CSV    = "https://robotsparebinindustries.com/orders.csv"

OUTPUT_DIR     = Path("output")
RECEIPT_DIR    = OUTPUT_DIR / "receipts"
SCREENSHOT_DIR = OUTPUT_DIR / "screenshots"

# Ensure folder structure exists on first run
for folder in (RECEIPT_DIR, SCREENSHOT_DIR):
    folder.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Main task
# --------------------------------------------------------------------------- #

@task
def order_robots_from_RobotSpareBin():
    """
    Orders robots from RobotSpareBin Industries Inc.
    Saves the order HTML receipt as a PDF file.
    Saves the screenshot of the ordered robot.
    Embeds the screenshot of the robot to the PDF receipt.
    Creates ZIP archive of the receipts and the images.
    
    """
    browser.configure(slowmo=0)

    # 1. Chiedo l’URL all’utente
    orders_csv_url = URL_CSV #get_user_input_url()          # ← prompt Assistant

    # 2. Scarico e leggo il CSV dall’URL indicato
    orders = download_and_read_orders(orders_csv_url)

    # 3. Procedo col resto della logica
    open_robot_order_website()
    for order in orders:
        process_single_order(order)

    archive_receipts()




def get_user_input_url():
    assistant = Assistant()
    assistant.add_heading("Robot Orders URL")
    assistant.add_text_input("orders_url", placeholder="Inserisci l’URL CSV")
    assistant.add_submit_buttons("Invia", default="Invia")
    result = assistant.run_dialog()
    return result.orders_url


# --------------------------------------------------------------------------- #
def download_and_read_orders(url: str):            # ← parametro aggiunto
    csv_target = OUTPUT_DIR / "orders.csv"
    HTTP().download(url, target_file=str(csv_target), overwrite=True)
    return Tables().read_table_from_csv(csv_target, header=True)

def open_robot_order_website():
    """Navigate to the robot order page and dismiss the startup modal."""
    browser.goto(URL_SHOP)
    close_annoying_modal()

def close_annoying_modal():
    """Dismiss the modal that appears on page load (button text: 'OK')."""
    page = browser.page()
    try:
        page.click("text=OK", timeout=2_000)
    except Exception:
        # Already closed or selector not found – ignore and continue
        pass

def process_single_order(order_row: dict):
    """Handle one CSV row from start to finish."""
    page = browser.page()

    # --- Fill the form ---------------------------------------------------- #
    page.select_option("#head", str(order_row["Head"]))
    page.check(f"#id-body-{order_row['Body']}")
    page.get_by_placeholder("Enter the part number for the legs").fill(
        str(order_row["Legs"])
    )
    page.fill("#address", order_row["Address"])

    # --- Click [Order] with robust retry ---------------------------------- #
    MAX_RETRIES = 5
    for attempt in range(1, MAX_RETRIES + 1):
        page.click("#order")

        # Wait up to 5 s for a receipt; if not visible, assume failure.
        try:
            page.locator("#receipt").wait_for(state="visible", timeout=5_000)
            break                        # success 🎉
        except Exception:
            # Optional: close the red 'Order failed' banner if it appeared
            try:
                page.click("div.alert-danger >> text=Order failed", timeout=500)
            except Exception:
                pass                    # banner not present
    else:
        raise RuntimeError(f"Order failed after {MAX_RETRIES} attempts")

    # --- Save artefacts --------------------------------------------------- #
    order_no        = order_row["Order number"]
    pdf_path        = save_receipt(order_no)
    screenshot_path = screenshot_robot(order_no)
    embed_screenshot_to_receipt(pdf_path, screenshot_path)

    # --- Reset for next order -------------------------------------------- #
    page.click("#order-another")
    close_annoying_modal()

def save_receipt(order_number: str) -> str:
    """Convert the HTML receipt into a PDF and return its path."""
    receipt_html = browser.page().locator("#receipt").inner_html()
    pdf_path = RECEIPT_DIR / f"robot_order_{order_number}.pdf"
    PDF().html_to_pdf(receipt_html, str(pdf_path))
    return str(pdf_path)

def screenshot_robot(order_number: str) -> str:
    """Capture the robot preview image and return its path."""
    shot_path = SCREENSHOT_DIR / f"robot_{order_number}.png"
    browser.page().locator("#robot-preview-image").screenshot(path=str(shot_path))
    return str(shot_path)

def embed_screenshot_to_receipt(pdf_path: str, screenshot_path: str):
    """Append the robot screenshot to the end of the PDF receipt."""
    PDF().add_files_to_pdf(
        files=[screenshot_path],
        target_document=pdf_path,
        append=True,
    )
    FileSystem().remove_file(screenshot_path)   # keep workspace clean

def archive_receipts():
    """Zip all receipts so Control Room sees only one artefact."""
    Archive().archive_folder_with_zip(
        str(RECEIPT_DIR),
        str(OUTPUT_DIR / "robot_orders.zip"),
        recursive=False,
    )
