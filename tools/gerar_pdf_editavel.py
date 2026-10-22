"""Gera o PDF editável de um material interativo do aluno (caderno, kit, preparação).

Uso: python tools/gerar_pdf_editavel.py HSC-MDSAP-M04_Caderno_de_Exercicios_ALUNO.html [saida.pdf]

Renderiza todas as seções do HTML em sequência (sem o gabarito, que não existe na versão
do aluno), troca cada campo de resposta por uma caixa e cada alternativa por um quadrado,
e depois cria campos de formulário (texto e marcação) por cima dessas caixas.
Requer: playwright (Chromium em PLAYWRIGHT_BROWSERS_PATH), pdfplumber, pypdf.
"""
import asyncio, os, re, sys, tempfile
from playwright.async_api import async_playwright

ANS_RGB = (250, 251, 254)   # fundo exclusivo das caixas de resposta
CK_RGB = (253, 254, 255)    # fundo exclusivo dos quadrados de alternativa

PRINT_CSS = """
html,body{background:#fff!important}
.toc,.footbar,#wm,.wm,.modal,.toast,.lock,.swm,.tools,.navbtns,.pager,.next,.prev,.save,.dlbar,footer{display:none!important}
.layout{display:block!important}
.page,#page{max-width:none!important;width:auto!important;box-shadow:none!important;border:none!important;margin:0!important;padding:0!important}
.wrapc{max-width:none!important;padding:0 !important}
.hero{min-height:auto!important;border-radius:0!important;margin-bottom:18px}
.hero .wrapc{padding:40px 36px!important}
section.ps{break-before:page;padding:6px 2px}
section.ps:first-of-type{break-before:auto}
.ans{display:block;width:100%;border:1px solid #9AA6D8;border-radius:0;background:rgb(250,251,254);margin:4px 0 2px;break-inside:avoid}
.opt{display:block!important;text-indent:0!important;cursor:default!important;background:none!important;border:none!important;break-inside:avoid}
.ck{display:inline-block;vertical-align:-1px;margin-right:7px;width:11px;height:11px;border:1px solid #4A6CF7;border-radius:0;background:rgb(253,254,255)}
.tw{overflow:visible!important}
table{break-inside:auto} tr{break-inside:avoid}
img{max-width:100%!important;break-inside:avoid}
"""

async def capture(src):
    html = open(src, encoding="utf-8").read().replace('"modoTeste": false', '"modoTeste": true')
    html = re.sub(r"@media print\{.*?\}\s*\}", "", html, count=1, flags=re.S)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
    tmp.write(html); tmp.close()
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1100, "height": 1400})
        await pg.goto("file://" + tmp.name)
        await pg.wait_for_timeout(1200)
        n = await pg.evaluate("document.querySelectorAll('#toc [data-go]').length")
        parts = []
        for i in range(n):
            await pg.evaluate(f"document.querySelectorAll('#toc [data-go]')[{i}].click()")
            await pg.wait_for_timeout(250)
            parts.append(await pg.evaluate("""()=>{
              const p=document.querySelector('#page').cloneNode(true);
              p.querySelectorAll('textarea').forEach(t=>{
                const r=parseInt(t.getAttribute('rows')||'3'); const d=document.createElement('div');
                d.className='ans'; d.dataset.k=t.dataset.k||''; d.style.height=Math.max(110,r*40)+'px'; t.replaceWith(d);});
              p.querySelectorAll('input[type=text],input:not([type])').forEach(t=>{
                const d=document.createElement('div'); d.className='ans'; d.dataset.k=t.dataset.k||''; d.style.height='28px'; t.replaceWith(d);});
              p.querySelectorAll('.opt').forEach(o=>{o.classList.remove('sel','fb-ok','fb-bad');
                const c=document.createElement('span'); c.className='ck'; o.prepend(c);});
              p.querySelectorAll('button,.navbtns,.pager').forEach(x=>x.remove());
              p.querySelectorAll('*').forEach(x=>{ if(x.children.length===0 && /ficam salvas neste navegador/i.test(x.textContent)) x.remove(); });
              return p.innerHTML;}"""))
        await pg.evaluate("""(parts)=>{
          const page=document.querySelector('#page');
          page.innerHTML=parts.map(h=>'<section class="ps">'+h+'</section>').join('');
        }""", parts)
        await pg.add_style_tag(content=PRINT_CSS)
        await pg.emulate_media(media="screen")
        await pg.wait_for_timeout(400)
        code = await pg.evaluate("CONFIG.codigo")
        title = await pg.evaluate("DOC.kicker+' · '+DOC.titulo")
        pdf = await pg.pdf(format="A4", print_background=True,
            margin={"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
            display_header_footer=True,
            header_template="<span></span>",
            footer_template=f"<div style='font-size:7.5px;color:#6E7480;width:100%;padding:0 14mm;display:flex;justify-content:space-between;font-family:Helvetica'><span>{code} · Uso exclusivo HSC Global Group. Reprodução proibida.</span><span><span class='pageNumber'></span>/<span class='totalPages'></span></span></div>")
        await b.close()
    os.unlink(tmp.name)
    return pdf, title

def add_fields(pdf_bytes, out, title):
    import io, pdfplumber
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject,
                               NumberObject, TextStringObject, BooleanObject, StreamObject)
    def near(c, rgb):
        if not c or len(c) != 3: return False
        try: c = [float(v) for v in c]
        except (TypeError, ValueError): return False
        return all(abs(float(a) * 255 - b) < 0.8 for a, b in zip(c, rgb))
    boxes = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as doc:
        for pi, page in enumerate(doc.pages):
            H = page.height
            for r in page.rects:
                col = r.get("non_stroking_color")
                kind = "t" if near(col, ANS_RGB) else "c" if near(col, CK_RGB) else None
                if kind and r["width"] > 6 and r["height"] > 6:
                    boxes.append((pi, kind, r["x0"], H - r["bottom"], r["x1"], H - r["top"]))
    # remove duplicatas (o Chromium pode emitir o mesmo retângulo mais de uma vez)
    seen, uniq = set(), []
    for b in boxes:
        k = (b[0], b[1]) + tuple(round(v) for v in b[2:])
        if k not in seen: seen.add(k); uniq.append(b)
    rd = PdfReader(io.BytesIO(pdf_bytes)); w = PdfWriter(); w.append(rd)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
    fref = w._add_object(font)
    zap = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                          NameObject("/BaseFont"): NameObject("/ZapfDingbats")}))
    fields = ArrayObject(); nt = nc = 0
    def ap(wd, ht, body, res):
        s = StreamObject(); s.set_data(body.encode())
        s.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                  NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(wd), FloatObject(ht)]),
                  NameObject("/Resources"): res})
        return w._add_object(s)
    for pi, kind, x0, y0, x1, y1 in uniq:
        page = w.pages[pi]
        if kind == "t":
            nt += 1; pad = 3
            rect = [x0 + pad, y0 + pad, x1 - pad, y1 - pad]
            a = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject(f"resposta_{nt:03d}"),
                NameObject("/Ff"): NumberObject(4096), NameObject("/F"): NumberObject(4),
                NameObject("/DA"): TextStringObject("/Helv 10 Tf 0.09 0.09 0.09 rg"),
                NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
                NameObject("/P"): page.indirect_reference})
        else:
            nc += 1; s = min(x1 - x0, y1 - y0); wd = ht = s
            res = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/ZaDb"): zap})})
            on = ap(wd, ht, f"q 0.29 0.42 0.97 rg BT /ZaDb {s*0.8:.2f} Tf {s*0.12:.2f} {s*0.2:.2f} Td (4) Tj ET Q", res)
            off = ap(wd, ht, "", DictionaryObject())
            a = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/FT"): NameObject("/Btn"), NameObject("/T"): TextStringObject(f"marcar_{nc:03d}"),
                NameObject("/F"): NumberObject(4), NameObject("/V"): NameObject("/Off"), NameObject("/AS"): NameObject("/Off"),
                NameObject("/DA"): TextStringObject("/ZaDb 0 Tf 0.29 0.42 0.97 rg"),
                NameObject("/MK"): DictionaryObject({NameObject("/CA"): TextStringObject("4")}),
                NameObject("/Rect"): ArrayObject([FloatObject(v) for v in (x0, y0, x0 + wd, y0 + ht)]),
                NameObject("/AP"): DictionaryObject({NameObject("/N"): DictionaryObject({NameObject("/Yes"): on, NameObject("/Off"): off})}),
                NameObject("/P"): page.indirect_reference})
        ref = w._add_object(a)
        if "/Annots" not in page: page[NameObject("/Annots")] = ArrayObject()
        page["/Annots"].append(ref); fields.append(ref)
    w._root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/Fields"): fields, NameObject("/NeedAppearances"): BooleanObject(True),
        NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g"),
        NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): fref, NameObject("/ZaDb"): zap})})})
    w.add_metadata({"/Title": title, "/Author": "HSC Global Group · HSC Academy"})
    with open(out, "wb") as f: w.write(f)
    return nt, nc, len(w.pages)

if __name__ == "__main__":
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else re.sub(r"_ALUNO\.html$", "_EDITAVEL.pdf", src)
    pdf, title = asyncio.run(capture(src))
    nt, nc, np_ = add_fields(pdf, out, title)
    print(f"{out}: {np_} páginas, {nt} campos de texto, {nc} alternativas marcáveis")
