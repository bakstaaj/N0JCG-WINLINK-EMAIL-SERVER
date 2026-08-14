from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "N0JCG_Winlink_Email_Server_End_User_Guide.docx"
ASSETS = ROOT / "docs" / "assets"
NAVY = RGBColor(10, 31, 68)
BLUE = RGBColor(21, 101, 192)
CYAN = RGBColor(0, 184, 217)
SLATE = RGBColor(83, 97, 113)
MIST = "F4F7FA"
PALE_BLUE = "E8F1FB"
PALE_CYAN = "E9FAFC"


def shade(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def shade_paragraph(paragraph, fill):
    p_pr = paragraph._p.get_or_add_pPr()
    shd = p_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        p_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def add_bottom_rule(paragraph, color="00B8D9", size="12", space="5"):
    p_pr = paragraph._p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        p_pr.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), size)
    bottom.set(qn("w:space"), space)
    bottom.set(qn("w:color"), color)
    borders.append(bottom)


def set_cell_margins(cell, top=100, start=120, bottom=100, end=120):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for side, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{side}"))
        if node is None:
            node = OxmlElement(f"w:{side}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_run(run, size=11, color=NAVY, bold=False, italic=False, font="Aptos"):
    run.font.name = font
    run._element.rPr.rFonts.set(qn("w:ascii"), font)
    run._element.rPr.rFonts.set(qn("w:hAnsi"), font)
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.bold = bold
    run.italic = italic


def style_document(doc):
    section = doc.sections[0]
    section.top_margin = Inches(0.72)
    section.bottom_margin = Inches(0.72)
    section.left_margin = Inches(0.78)
    section.right_margin = Inches(0.78)
    section.header_distance = Inches(0.32)
    section.footer_distance = Inches(0.32)
    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Aptos"
    normal._element.rPr.rFonts.set(qn("w:ascii"), "Aptos")
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Aptos")
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = NAVY
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.12
    for name, size, color, before, after in (("Heading 1", 18, NAVY, 14, 6), ("Heading 2", 13, BLUE, 10, 4), ("Heading 3", 11, NAVY, 8, 3)):
        st = styles[name]
        st.font.name = "Aptos Display"
        st._element.rPr.rFonts.set(qn("w:ascii"), "Aptos Display")
        st._element.rPr.rFonts.set(qn("w:hAnsi"), "Aptos Display")
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = color
        st.paragraph_format.space_before = Pt(before)
        st.paragraph_format.space_after = Pt(after)


def add_header_footer(doc):
    for section in doc.sections:
        section.different_first_page_header_footer = True
        first_header = section.first_page_header.paragraphs[0]
        first_header.text = ""
        header = section.header.paragraphs[0]
        header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r = header.add_run("N0JCG WINLINK EMAIL SERVER  |  END USER GUIDE")
        set_run(r, size=8, color=SLATE, bold=True)
        first_footer = section.first_page_footer.paragraphs[0]
        first_footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        set_run(first_footer.add_run("N0JCG Open Radio Platform  |  N0JCG Winlink Email Server v0.1.0    Page 1"), size=8, color=SLATE)
        add_bottom_rule(first_footer)
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = footer.add_run("N0JCG Open Radio Platform  |  Client-side appliance, not an RMS gateway")
        set_run(r, size=8, color=SLATE)


def add_callout(doc, title, body, fill=PALE_BLUE):
    table = doc.add_table(rows=1, cols=1)
    table.autofit = False
    table.columns[0].width = Inches(6.85)
    cell = table.cell(0, 0)
    shade(cell, fill)
    set_cell_margins(cell, top=150, start=180, bottom=150, end=180)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(3)
    set_run(p.add_run(title), size=10.5, color=NAVY, bold=True)
    p2 = cell.add_paragraph()
    p2.paragraph_format.space_after = Pt(0)
    set_run(p2.add_run(body), size=10, color=NAVY)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def add_bullets(doc, items):
    for item in items:
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.left_indent = Inches(0.28)
        p.paragraph_format.first_line_indent = Inches(-0.16)
        p.paragraph_format.space_after = Pt(3)
        set_run(p.add_run(item), size=10.5, color=NAVY)


def add_steps(doc, items):
    for idx, item in enumerate(items, 1):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Inches(0.32)
        p.paragraph_format.first_line_indent = Inches(-0.32)
        p.paragraph_format.space_after = Pt(5)
        set_run(p.add_run(f"{idx}.  "), size=10.5, color=CYAN, bold=True)
        set_run(p.add_run(item), size=10.5, color=NAVY)


def add_screenshot(doc, filename, caption, width=6.8):
    path = ASSETS / filename
    if path.exists():
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(str(path), width=Inches(width))
        cap = doc.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap.paragraph_format.space_after = Pt(8)
        set_run(cap.add_run(caption), size=8.5, color=SLATE, italic=True)


def add_table(doc, headers, rows, widths):
    table = doc.add_table(rows=1, cols=len(headers))
    table.autofit = False
    for i, (cell, header) in enumerate(zip(table.rows[0].cells, headers)):
        cell.width = Inches(widths[i])
        shade(cell, "0A1F44")
        set_cell_margins(cell)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        p = cell.paragraphs[0]
        set_run(p.add_run(header), size=9, color=RGBColor(255, 255, 255), bold=True)
    for ridx, row in enumerate(rows):
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].width = Inches(widths[i])
            shade(cells[i], "FFFFFF" if ridx % 2 else MIST)
            set_cell_margins(cells[i])
            cells[i].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            p = cells[i].paragraphs[0]
            set_run(p.add_run(value), size=9.5, color=NAVY)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def build():
    doc = Document()
    style_document(doc)
    add_header_footer(doc)

    # Cover: match the N0JCG Scanner handbook family layout.
    banner = doc.add_table(rows=1, cols=1)
    banner.autofit = False
    banner.columns[0].width = Inches(6.85)
    banner_cell = banner.cell(0, 0)
    shade(banner_cell, "0A1F44")
    set_cell_margins(banner_cell, top=220, start=220, bottom=220, end=220)
    banner_para = banner_cell.paragraphs[0]
    banner_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    banner_para.add_run().add_picture(str(ROOT / "assets" / "brand" / "N0JCG_Header_Dark_Approved.png"), width=Inches(4.7))
    doc.add_paragraph().paragraph_format.space_after = Pt(26)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(12)
    set_run(p.add_run("OPERATOR HANDBOOK"), size=10.5, color=CYAN, bold=True, font="Aptos Display")
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(8)
    set_run(p.add_run("N0JCG Winlink Email Server"), size=25, color=NAVY, bold=True, font="Aptos Display")
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(12)
    set_run(p.add_run("Installation, client setup, webmail operation, and troubleshooting"), size=13, color=SLATE)
    rule = doc.add_paragraph()
    rule.paragraph_format.space_before = Pt(18)
    rule.paragraph_format.space_after = Pt(20)
    add_bottom_rule(rule, size="14", space="1")
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(26)
    set_run(p.add_run("A complete guide to the Raspberry Pi client appliance, Winlink mailbox access, DigiRig Mobile integration, and safe Packet RMS operation."), size=11, color=NAVY)
    add_table(doc, ["Release", "Publication"], [
        ("0.1.0", "August 2026"),
        ("PRODUCT ROLE\nClient-side Winlink email", "AUDIENCE\nOperators and mailbox users"),
    ], [3.42, 3.42])
    doc.add_page_break()

    doc.add_heading("At a glance", level=1)
    doc.add_paragraph("N0JCG Winlink Email Server places the operator console, Winlink Client mailbox, local drafts and queues, and the webmail experience on one Raspberry Pi appliance. The external radio and Packet RMS gateway provide the radio transport.")
    add_callout(doc, "THE OPERATING MODEL", "Browser -> N0JCG webmail and operator console -> N0JCG management layer -> Winlink Client mailbox and transport -> DigiRig Mobile -> radio -> external Packet RMS gateway.")
    add_table(doc, ["Surface", "Use it for", "Address"], [
        ("Operator console", "Configuration, status, diagnostics, and safety controls", "http://PI-IP/ui/"),
        ("User webmail", "Inbox, compose, drafts, folders, signature, and queue", "http://PI-IP/webmail/"),
        ("Deployment helper", "Install and upgrade from MSYS2 Bash", "./deploy/push_to_pi.sh"),
    ], [1.35, 3.5, 1.95])
    doc.add_heading("Capability boundary", level=2)
    add_bullets(doc, [
        "The appliance can be installed and configured before the radio is connected.",
        "USB detection is evidence that the operating system saw a device; it is not proof of audio, PTT, packet, or RF operation.",
        "Transmission stays disabled until the operator explicitly configures and verifies the radio path.",
    ])
    doc.add_page_break()

    doc.add_heading("1. Getting started", level=1)
    doc.add_heading("Required hardware", level=2)
    add_bullets(doc, [
        "Raspberry Pi 4 with 64-bit Debian/Raspberry Pi OS and reliable power",
        "microSD card with the operating system installed",
        "DigiRig Mobile audio/PTT interface and USB cable",
        "Compatible amateur radio with packet/data audio and PTT connections",
        "Radio-specific interface cables",
        "Ethernet or Wi-Fi network connection",
        "A workstation on the same network running MSYS2 Bash, Git, OpenSSH, and sshpass",
    ])
    doc.add_heading("Helpful accessories", level=2)
    add_bullets(doc, ["Powered USB hub for multiple peripherals", "Keyboard and display for recovery", "Labeled cables", "UPS or clean 5 V power for unattended use"])
    add_callout(doc, "PRE-RADIO SETUP", "Software, branding, accounts, and webmail can be prepared before the radio is connected. Complete the software setup first, then bring up the radio as a controlled receive-first procedure.", PALE_CYAN)
    doc.add_page_break()

    doc.add_heading("2. Install from MSYS2 Bash", level=1)
    doc.add_paragraph("The official deployment helper copies the complete application to the Pi, runs the installer, and verifies that the webmail service is active.")
    doc.add_heading("Prepare the workstation", level=2)
    add_steps(doc, [
        "Open MSYS2 UCRT64 or MSYS2 MSYS Bash.",
        "Install the deployment tools with: pacman -S --needed git openssh sshpass",
        "Clone the repository: git clone https://github.com/bakstaaj/N0JCG-WINLINK-EMAIL-SERVER.git",
        "Enter the repository: cd N0JCG-WINLINK-EMAIL-SERVER",
    ])
    doc.add_heading("Run the guided deployment", level=2)
    p = doc.add_paragraph()
    set_run(p.add_run("./deploy/push_to_pi.sh"), size=10, color=RGBColor(255,255,255), font="Consolas")
    shade_paragraph(p, "0A1F44")
    doc.add_paragraph("The helper prompts for the Raspberry Pi IP address, SSH username, and SSH password. The default address is 192.168.68.149 and the default SSH username is pi, but these values are intended to be changed for another installation.")
    doc.add_heading("Operator authentication", level=2)
    doc.add_paragraph("During the initial install, the installer asks for an Nginx operator-console username, password, and password confirmation. Existing operator authentication is preserved during normal upgrades.")
    doc.add_heading("Wi-Fi, hotspot fallback, and USB gadget", level=2)
    doc.add_paragraph("To configure connectivity during deployment, add --configure-connectivity. The helper configures the secured N0JCG-WES hotspot and confirms its password. The Pi activates the hotspot at 192.168.50.1 with leases from 192.168.50.100 through 192.168.50.200. The initial password is Password; change it during installation when prompted.")
    p = doc.add_paragraph()
    set_run(p.add_run("./deploy/push_to_pi.sh 192.168.68.149 pi --configure-connectivity"), size=10, color=RGBColor(255,255,255), font="Consolas")
    shade_paragraph(p, "0A1F44")
    doc.add_paragraph("The Pi 4 USB Ethernet gadget is available at 192.168.60.1 through the USB-C power/data port. The blue USB host ports are not gadget ports.")
    add_callout(doc, "RECONFIGURE OPERATOR AUTH", "Use ./deploy/push_to_pi.sh PI-IP PI-USER --configure-operator-auth when the operator account must be intentionally changed.", PALE_BLUE)
    doc.add_heading("Verify", level=2)
    add_steps(doc, [
        "Open http://PI-IP/ui/ from a workstation on the same network.",
        "Open http://PI-IP/webmail/.",
        "Confirm the deployment helper reports that the API was deployed and n0jcg-webmail.service is active.",
    ])
    doc.add_page_break()

    doc.add_heading("3. First-time setup", level=1)
    doc.add_heading("Operator console", level=2)
    doc.add_paragraph("Sign in to the operator console with the credentials created by the installer. Review the host identity, service state, DigiRig/USB detection, Winlink Client readiness, radio profile, Packet settings, and diagnostics before connecting RF equipment.")
    doc.add_heading("Webmail account", level=2)
    doc.add_paragraph("Open the webmail address and select Sign in with Winlink. Enter the user’s Winlink email address and secure-login password. The appliance validates the credentials against Winlink before opening the private local mailbox for that callsign.")
    add_callout(doc, "PRIVATE MAILBOX MODEL", "Mailbox access is isolated by callsign. A user cannot select another user’s mailbox from a URL or browser control.", PALE_CYAN)
    add_screenshot(doc, "webmail-inbox.png", "Figure 1. N0JCG webmail inbox and mailbox status on the client appliance.")
    doc.add_page_break()

    doc.add_heading("4. Everyday webmail", level=1)
    doc.add_heading("Compose a message", level=2)
    doc.add_paragraph("Select New message to open a blank form. Enter the Winlink recipient, subject, and message body. Select Save draft when work should remain local, or use the send control when the operator has enabled a verified transport path.")
    add_screenshot(doc, "compose-message.png", "Figure 2. New message panel with attachment controls and the N0JCG navigation shell.")
    doc.add_heading("Drafts, attachments, and signature", level=2)
    add_bullets(doc, [
        "The Drafts counter updates after saving. Open a draft to continue editing or delete it from the Drafts panel.",
        "Attachments are limited to 100 KB. A warning appears above 10 KB because packet-radio transfer may be slow.",
        "Select Signature to save a per-user signature. It is restored after the next login for that Winlink callsign.",
        "Use Log out when leaving a shared workstation.",
    ])
    doc.add_heading("Folders", level=2)
    doc.add_paragraph("Custom folders are shown indented under Inbox. Create and delete folders from Manage folders, then drag an Inbox message onto a folder to organize it.")
    doc.add_page_break()

    doc.add_heading("5. Connect and verify the radio", level=1)
    add_steps(doc, [
        "Power off the radio and Raspberry Pi before changing audio/PTT cabling.",
        "Connect the DigiRig Mobile to the radio with the correct radio-specific cables.",
        "Connect the DigiRig Mobile directly to a Raspberry Pi USB 3 port or powered USB hub.",
        "Keep the Pi 4 USB-C power/data port available for the USB Ethernet gadget; it is not a DigiRig host port.",
        "Power the radio, DigiRig Mobile, and Raspberry Pi.",
        "Confirm the operator console reports expected USB/audio/serial devices as Detected.",
        "Configure the radio profile, audio levels, PTT method, and selected Packet RMS gateway.",
        "Perform receive-only checks before enabling transmit behavior.",
        "Enable transmission only after the operator has verified the radio path and local regulations.",
    ])
    add_callout(doc, "SAFETY RULE", "Do not enable transmit merely because a DigiRig or USB device is present. Detection is hardware evidence only; PTT and RF behavior require separate verification.", "FFF4D6")
    doc.add_heading("What the gateway means here", level=2)
    doc.add_paragraph("The Packet RMS gateway is an external Winlink service reached over the radio path. This product is the client-side email server and does not host or replace that gateway.")
    doc.add_page_break()

    doc.add_heading("6. Troubleshooting", level=1)
    doc.add_heading("Mailbox unavailable", level=2)
    doc.add_paragraph("This means the Winlink account was authenticated, but the local mailbox service is not currently available. It is not a request for the user to type a command. Connect the radio and run a mailbox synchronization, then select Refresh. If the message persists, the operator should review the client service and diagnostics.")
    doc.add_heading("Login fails", level=2)
    doc.add_paragraph("Confirm the Winlink address and secure-login password. Check the Pi network connection and system time. Repeated failed attempts are rate-limited for protection.")
    doc.add_heading("Device detected but not ready", level=2)
    doc.add_paragraph("Check the correct radio cable, audio routing, serial/PTT configuration, and radio power. Do not enable transmit based on USB detection alone.")
    doc.add_heading("Deployment verification fails", level=2)
    doc.add_paragraph("Rerun the deployment helper from MSYS2 Bash and confirm the Pi IP address, SSH username, and SSH password. Use the diagnostic helper for a status report:")
    p = doc.add_paragraph()
    set_run(p.add_run("./deploy/inspect_pi_webmail.sh"), size=10, color=RGBColor(255,255,255), font="Consolas")
    shade_paragraph(p, "0A1F44")
    doc.add_page_break()

    doc.add_heading("Quick reference", level=1)
    add_table(doc, ["Task", "Location"], [
        ("Deploy or upgrade", "./deploy/push_to_pi.sh in MSYS2 Bash"),
        ("Diagnostics", "./deploy/inspect_pi_webmail.sh"),
        ("Operator console", "http://PI-IP/ui/"),
        ("User webmail", "http://PI-IP/webmail/"),
        ("Default SSH user", "pi"),
        ("Product host role", "PI-WINLINK"),
        ("Maximum attachment", "100 KB"),
        ("Attachment warning", "Above 10 KB"),
    ], [2.1, 4.7])
    add_callout(doc, "SUPPORT CHECKLIST", "When requesting help, include the appliance IP address, the page being used, the exact visible message, whether the radio is connected, and the output of inspect_pi_webmail.sh. Never include passwords.", PALE_BLUE)
    doc.save(OUT)
    print(OUT)


if __name__ == "__main__":
    build()
