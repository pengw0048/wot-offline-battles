"""One reading of #1513's own shop prices, shared by every reader of them.

``<price>`` in an item definition is a credit amount, unless it carries a
``<gold/>`` marker, in which case the same number is a gold amount. That is
exactly how the client decides a vehicle is premium while reading
``list.xml``, and it is the only place the amounts survive: ``items.vehicles``
parses them at startup and then resets ``_g_prices`` to ``None``.

The price baker writes the client-side catalogue from this, and the launcher
reads the installed client with it to build the gold vehicle shop. They must
agree, so the rule lives here rather than in either of them.
"""

try:
    import packed_xml
except ImportError:  # pragma: no cover - exercised by the packaged launcher
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import packed_xml


def children(section):
    """Yield ``(name, value)`` for one packed element, names as text.

    The packed dictionary carries the XML namespace declarations too, and they
    are not fields.
    """
    for raw_name, value in (getattr(section, 'children', ()) or ()):
        name = text(raw_name)
        if ':' in name:
            continue
        yield name, value


def element(value):
    """Return the nested element of one packed value, or None."""
    if value is None or value.value_type != packed_xml.TYPE_ELEMENT:
        return None
    return value.value


def text(value):
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return '' if value is None else str(value)


def number(value):
    if not value:
        return 0
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def own_text(nested):
    """Return the text an element carries beside its children.

    A ``<price>`` with a ``<gold/>`` child keeps its amount here rather than in
    a child, so the amount and the currency marker arrive together.
    """
    return text(getattr(nested.value, 'value', b'')).strip()


def read_price(section):
    """Return ``(credits, gold, not_in_shop)``, or None when unpriced."""
    price = None
    not_in_shop = False
    for name, value in children(section):
        if name == 'price':
            nested = element(value)
            amount = number(
                own_text(nested) if nested is not None else text(value.value))
            is_gold = nested is not None and any(
                child == 'gold' for child, unused in children(nested))
            price = (0, amount) if is_gold else (amount, 0)
        elif name == 'notInShop':
            raw = value.value
            not_in_shop = (raw is True or
                           text(raw).strip().lower() in ('true', '1'))
    if price is None:
        return None
    return (price[0], price[1], not_in_shop)
