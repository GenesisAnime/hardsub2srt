#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""srt2ass.py — SRT -> ASS donusturucu (hardsub2srt sisteminin bagimsiz ASS ciktisi)

Ceviri kaynagi her zaman SRT'dir (dokunulmaz). ASS, stil/konum korunacak
senaryolar icindir: anime topluluklarinda yaygin format, ust/tabela yazilari
ayri konumlanabilir; font/kenarlik/golge her oynaticida ayni render edilir.

Kullanim:
    py -3 srt2ass.py "1. Bolum.srt" -o "1. Bolum.ass"
    py -3 srt2ass.py "video.srt" -o "video.ass" --width 1920 --height 1080
    py -3 srt2ass.py tabela.srt -o tabela.ass --style top

Davranis:
- Her SRT blogu tek Dialogue satiri olur; SRT'nin ikinci satiri ASS'te "\\N"
  ile birlesir.
- HTML etiketleri (<i>, <b>, <font ...>) temizlenir; {"\\an8"} gibi SRT konum
  etiketleri ASS'te de gecerli oldugundan aynen korunur.
- Zamanlar ASS salise biçimine (H:MM:SS.cc) cevrilir (00:00:05,464 ->
  0:00:05.46).
- Tek stil "Dialog": alt-orta (Alignment=2), Arial 48, beyaz + siyah
  Outline 3 + Shadow 1, MarginV 40 — hardsub gorunumune yakin.
- --style top: ayni stili ust-orta (Alignment=8) yazar (tabela/jenerik icin).

Bilinen sinir: SRT'deki dengesiz '{' / '}' karakterleri (OCR gurultusu,
jenerik bloklari) ASS'te override blogu gibi yorumlanabilir; ceviri oncesi bu
cop bloklar zaten silinir.
"""

import argparse
import html
import re
import sys
from pathlib import Path

# SRT zaman satiri: 00:00:05,464 --> 00:00:11,637 (virgul VEYA nokta toleransli)
TIME_RE = re.compile(
    r'(\d\d):(\d\d):(\d\d)[,.](\d\d\d)\s*-->\s*'
    r'(\d\d):(\d\d):(\d\d)[,.](\d\d\d)'
)
# <i>, </b>, <font color="#fff"> gibi HTML etiketleri
HTML_TAG_RE = re.compile(r'</?[a-zA-Z][^>]*>')

# ASS numpad hizasi: 2 = alt-orta, 8 = ust-orta
ALIGN = {'bottom': 2, 'top': 8}

V4PLUS_FORMAT = (
    'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
    'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, '
    'ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
    'Alignment, MarginL, MarginR, MarginV, Encoding'
)

EVENT_FORMAT = (
    'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, '
    'Effect, Text'
)


def make_style_line(align: int) -> str:
    """Tek stil 'Dialog': Arial 48, beyaz, siyah Outline 3, Shadow 1, MarginV 40."""
    return (
        'Style: Dialog,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,'
        f'0,0,0,0,100,100,0,0,1,3,1,{align},60,60,40,1'
    )


def read_text(path: Path) -> str:
    """UTF-8 (BOM'lu/BOM'suz), olmazsa cp1254/latin-1 ile oku."""
    data = path.read_bytes()
    for enc in ('utf-8-sig', 'cp1254', 'latin-1'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def clean_text(line: str) -> str:
    """HTML etiketlerini temizle, HTML varliklarini coz, bosluklari sadelestir."""
    line = HTML_TAG_RE.sub('', line)
    line = html.unescape(line)
    return re.sub(r'\s+', ' ', line).strip()


def ass_time(t) -> str:
    """(saat, dakika, saniye, milisaniye) -> ASS 'H:MM:SS.cc' (salise)."""
    h, m, s, ms = t
    return f'{h}:{m:02d}:{s:02d}.{ms // 10:02d}'


def parse_srt(text: str):
    """SRT metnini (baslangic, bitis, [temiz satirlar]) bloklarina ayirir.

    Index satirlari atlanir (zaman satirindan onceki her sey), zamansiz /
    metinsiz bloklar dusurulur.
    """
    blocks = []
    for raw in re.split(r'\n\s*\n', text.strip()):
        lines = raw.replace('\r', '').split('\n')
        tidx = next((i for i, ln in enumerate(lines) if TIME_RE.search(ln)), None)
        if tidx is None:
            continue  # zaman satiri yok (index/gurultu) -> atla
        m = TIME_RE.search(lines[tidx])
        start = tuple(int(m.group(i)) for i in range(1, 5))
        end = tuple(int(m.group(i)) for i in range(5, 9))
        text_lines = [clean_text(ln) for ln in lines[tidx + 1:]]
        text_lines = [ln for ln in text_lines if ln]
        if not text_lines:
            continue  # bos blok -> atla
        blocks.append((start, end, text_lines))
    return blocks


def build_ass(blocks, title: str, width: int, height: int, align: int):
    """ASS satirlarini kur (CRLF ile birlestirilip yazilacak)."""
    out = [
        '[Script Info]',
        '; srt2ass.py ile uretildi (hardsub2srt sistemi)',
        f'Title: {title}',
        'ScriptType: v4.00+',
        'WrapStyle: 0',
        'ScaledBorderAndShadow: yes',
        f'PlayResX: {width}',
        f'PlayResY: {height}',
        '',
        '[V4+ Styles]',
        V4PLUS_FORMAT,
        make_style_line(align),
        '',
        '[Events]',
        EVENT_FORMAT,
    ]
    for start, end, text_lines in blocks:
        text = '\\N'.join(text_lines)
        out.append(
            f'Dialogue: 0,{ass_time(start)},{ass_time(end)},'
            f'Dialog,,0,0,0,,{text}'
        )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='SRT -> ASS donusturucu (hardsub2srt sistemi, bagimsiz arac)')
    ap.add_argument('srt', help='girdi .srt dosyasi (zorunlu)')
    ap.add_argument('-o', '--output',
                    help='cikti .ass yolu (verilmezse .srt ile ayni ad)')
    ap.add_argument('--width', type=int, default=1920,
                    help='PlayResX (varsayilan 1920)')
    ap.add_argument('--height', type=int, default=1080,
                    help='PlayResY (varsayilan 1080)')
    ap.add_argument('--style', choices=('bottom', 'top'), default='bottom',
                    help='blok konumu: bottom=alt-orta (varsayilan), '
                         'top=ust-orta (tabela/jenerik)')
    args = ap.parse_args(argv)

    srt_path = Path(args.srt)
    if not srt_path.is_file():
        print(f'HATA: girdi bulunamadi: {srt_path}', file=sys.stderr)
        return 2
    out_path = Path(args.output) if args.output else srt_path.with_suffix('.ass')

    blocks = parse_srt(read_text(srt_path))
    if not blocks:
        print(f'HATA: {srt_path} icinde altyazi blogu bulunamadi', file=sys.stderr)
        return 1

    lines = build_ass(blocks, srt_path.name, args.width, args.height,
                      ALIGN[args.style])
    out_path.write_text('\r\n'.join(lines) + '\r\n',
                        encoding='utf-8-sig', newline='')

    konum = 'alt-orta' if args.style == 'bottom' else 'ust-orta'
    print(f'[ass] {len(blocks)} blok -> {out_path} '
          f'(Dialog, {konum}, {args.width}x{args.height})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
