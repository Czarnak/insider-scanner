"""Canonical US trade provenance shared by import, mapping and scan services."""

SEC_EDGAR_SOURCE = "sec_edgar"
US_SOURCES = frozenset({"secform4", "openinsider", SEC_EDGAR_SOURCE})
