"""Pure-unit tests for general/foreground_importer.py validation.

No brightway needed: load_foreground_csv() takes its biosphere / uslci databases
as plain iterables of dict-like activities, so we pass small lists of dicts as
fakes and exercise the CSV parsing + validation rules directly.
"""
import csv

import pytest

from fedefl_bw25 import foreground_importer as fi

HEADER = [
    "process_name", "exchange_type", "flow_uuid", "provider_uuid",
    "flow_name", "amount", "unit", "is_ref",
]

# Fake databases: items must support item-access ["code"] and .get("unit", "").
BIO_DB = [{"code": "bio-kg", "unit": "kg"}]      # one mass biosphere flow
USLCI_DB = [{"code": "prov-uslci"}]              # one background provider


def _write(tmp_path, rows, header=HEADER):
    p = tmp_path / "fg.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return p


def _row(process="Widget", etype="production", flow_uuid="", provider="",
         flow_name="x", amount="1", unit="kg", is_ref="false"):
    return [process, etype, flow_uuid, provider, flow_name, amount, unit, is_ref]


def _valid_rows():
    return [
        _row(etype="production", amount="1", unit="kg", is_ref="true"),
        _row(etype="biosphere", flow_uuid="bio-kg", amount="2", unit="kg"),
    ]


# --- fg_uuid -----------------------------------------------------------------

def test_fg_uuid_is_deterministic_and_name_normalized():
    assert fi.fg_uuid("Widget") == fi.fg_uuid("Widget")
    # Whitespace + case are normalized before hashing.
    assert fi.fg_uuid("Widget") == fi.fg_uuid("  widget ")
    assert fi.fg_uuid("Widget") != fi.fg_uuid("Gadget")


# --- structural CSV errors ---------------------------------------------------

def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        fi.load_foreground_csv(tmp_path / "nope.csv", BIO_DB, USLCI_DB)


def test_missing_required_columns_raises(tmp_path):
    p = _write(tmp_path, [["Widget", "production"]], header=["process_name", "exchange_type"])
    with pytest.raises(ValueError, match="missing required columns"):
        fi.load_foreground_csv(p, BIO_DB, USLCI_DB)


def test_header_only_raises(tmp_path):
    p = _write(tmp_path, [])
    with pytest.raises(ValueError, match="no data rows"):
        fi.load_foreground_csv(p, BIO_DB, USLCI_DB)


# --- happy path --------------------------------------------------------------

def test_valid_minimal_csv_parses(tmp_path):
    p = _write(tmp_path, _valid_rows())
    out = fi.load_foreground_csv(p, BIO_DB, USLCI_DB)
    assert set(out) == {"Widget"}
    assert out["Widget"]["uuid"] == fi.fg_uuid("Widget")
    assert len(out["Widget"]["exchanges"]) == 2


# --- validation rules --------------------------------------------------------

def test_invalid_exchange_type_raises(tmp_path):
    rows = [_row(etype="production", amount="1", is_ref="true"),
            _row(etype="emission", flow_uuid="bio-kg", amount="1")]
    p = _write(tmp_path, rows)
    with pytest.raises(ValueError, match="invalid exchange_type"):
        fi.load_foreground_csv(p, BIO_DB, USLCI_DB)


def test_non_numeric_amount_raises(tmp_path):
    rows = [_row(etype="production", amount="1", is_ref="true"),
            _row(etype="biosphere", flow_uuid="bio-kg", amount="lots")]
    p = _write(tmp_path, rows)
    with pytest.raises(ValueError, match="not a valid number"):
        fi.load_foreground_csv(p, BIO_DB, USLCI_DB)


def test_exactly_one_reference_required(tmp_path):
    # zero is_ref=true rows
    rows = [_row(etype="production", amount="1", is_ref="false")]
    p = _write(tmp_path, rows)
    with pytest.raises(ValueError, match="exactly 1 is_ref=true"):
        fi.load_foreground_csv(p, BIO_DB, USLCI_DB)


def test_biosphere_flow_must_exist_in_bio_db(tmp_path):
    rows = [_row(etype="production", amount="1", is_ref="true"),
            _row(etype="biosphere", flow_uuid="not-a-flow", amount="1", unit="kg")]
    p = _write(tmp_path, rows)
    with pytest.raises(ValueError, match="not found in biosphere-fedefl"):
        fi.load_foreground_csv(p, BIO_DB, USLCI_DB)


def test_biosphere_unit_flow_property_mismatch(tmp_path):
    # bio-kg's reference unit is kg (mass); MJ is energy -> incompatible.
    rows = [_row(etype="production", amount="1", is_ref="true"),
            _row(etype="biosphere", flow_uuid="bio-kg", amount="1", unit="MJ")]
    p = _write(tmp_path, rows)
    with pytest.raises(ValueError, match="incompatible"):
        fi.load_foreground_csv(p, BIO_DB, USLCI_DB)


def test_technosphere_requires_provider(tmp_path):
    rows = [_row(etype="production", amount="1", is_ref="true"),
            _row(etype="technosphere", provider="", amount="1")]
    p = _write(tmp_path, rows)
    with pytest.raises(ValueError, match="requires a provider_uuid"):
        fi.load_foreground_csv(p, BIO_DB, USLCI_DB)


def test_production_amount_must_be_positive(tmp_path):
    rows = [_row(etype="production", amount="0", is_ref="true")]
    p = _write(tmp_path, rows)
    with pytest.raises(ValueError, match="amount must be > 0"):
        fi.load_foreground_csv(p, BIO_DB, USLCI_DB)


# --- convert_to_ref_unit: the unit column is load-bearing --------------------
# Guards the defect found 2026-08-05: amounts were used verbatim against the
# counterpart's reference unit, so "200 g" of a kg-based provider was read as
# 200 kg. A flow-property check alone does not catch it — g and kg are both
# mass — which is why conversion, not just validation, is required.

def test_matching_units_pass_through_untouched():
    amt, err, note = fi.convert_to_ref_unit(0.2, "kg", "kg")
    assert (amt, err, note) == (0.2, None, None)


def test_subunit_is_converted_not_passed_through():
    amt, err, note = fi.convert_to_ref_unit(200.0, "g", "kg")
    assert err is None and amt == pytest.approx(0.2)
    assert "converted" in note


def test_conversion_is_case_insensitive():
    assert fi.convert_to_ref_unit(1.0, "KG", "kg")[0] == pytest.approx(1.0)
    assert fi.convert_to_ref_unit(1000.0, "G", "kg")[0] == pytest.approx(1.0)


def test_cross_property_units_are_refused():
    amt, err, note = fi.convert_to_ref_unit(0.2, "MJ", "kg")
    assert note is None and "incompatible" in err


def test_unknown_unit_is_refused_not_passed_through():
    # The ledger-#7 principle: an unconverted unit is a wrong number wearing a
    # plausible one's clothes, so refuse rather than assume 1.0.
    amt, err, note = fi.convert_to_ref_unit(1.0, "furlong", "kg")
    assert note is None and err is not None


def test_volume_and_energy_convert_within_property():
    assert fi.convert_to_ref_unit(1000.0, "l", "m3")[0] == pytest.approx(1.0)
    assert fi.convert_to_ref_unit(1.0, "kWh", "MJ")[0] == pytest.approx(3.6)


def test_empty_reference_unit_is_left_alone():
    # Nothing to convert onto — must not silently scale.
    assert fi.convert_to_ref_unit(5.0, "kg", "") == (5.0, None, None)
