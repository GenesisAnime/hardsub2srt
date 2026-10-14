"""Yol kapsama karşılaştırmaları için tek kaynak.

Windows'ta aynı dizin iki farklı metinle ifade edilebilir:

* **8.3 kısa ad** — `C:\\Users\\RUNNER~1` ile `C:\\Users\\runneradmin`
* **sembolik bağ / junction** — köprü yolu ile hedefi

Kapılar (`_contained`, `is_relative_to`, `relative_to`) karşılaştırmanın bir
tarafını `Path.resolve()` ile normalize edip diğer tarafı ham bıraktığında, kök
ile kökün altındaki dosya "akrabasız" görünüyor ve fail-closed kapı **geçerli**
dosyayı reddediyordu:

    ValueError: 'C:\\Users\\runneradmin\\...\\verified-ocr-dataset-...' is not
    in the subpath of 'C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\...'

Bu modül karşılaştırmanın **iki tarafını da** aynı biçime getirir. Ölçüm:
`tests/test_path_scope.py` içindeki kısa ad ve bağ senaryoları.

Bilinçli sınır: normalleştirme bağları **çözer**. Bağın kendisini yasaklamak
isteyen kod `lstat` yürüyüşünü ham yol üzerinde yapmaya devam etmelidir; bu
modül yalnız "aynı ağacın iki gösterimi" sorununu çözer.
"""

from __future__ import annotations

import os
from pathlib import Path


def normalize(path) -> Path:
    """Yolu karşılaştırılabilir tek biçime getir (kısa ad/bağ açılır).

    Var olma zorunluluğu koymaz; varlık denetimi çağıranın işidir. Çözülemeyen
    yol (`OSError`, `RuntimeError`, `ValueError`) olduğu gibi yükseltilir ki
    çağıranların mevcut `except` blokları çalışmaya devam etsin.
    """
    return Path(os.fspath(path)).resolve(strict=False)


def relative_to(path, root) -> Path:
    """`root`'a göre göreli yol; aynı ağacın iki gösteriminde de çalışır.

    Dışarıdaki yol için `ValueError` yükseltir (fail-closed çağıranların
    beklediği davranış).
    """
    return normalize(path).relative_to(normalize(root))


def is_within(path, root) -> bool:
    """`path` gerçekten `root` altında mı? İki taraf da normalize edilir.

    Çözülemeyen yol reddedilir (`False`) — kapı fail-closed kalır, ama geçerli
    kısa ad/bağ gösterimi artık yanlış reddedilmez.
    """
    try:
        relative_to(path, root)
    except ValueError:
        return False
    except (OSError, RuntimeError):
        return False
    return True
