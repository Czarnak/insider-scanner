"""Shared test fixtures: sample HTML for scrapers."""

from __future__ import annotations

# -----------------------------------------------------------------------
# secform4.com sample HTML
# -----------------------------------------------------------------------

SECFORM4_HTML = """
<html>
<body>
<div class="sort-table-wrapper">
<table class="sort-table" id="filing_table" style="width:100%">
<thead class="transactionHead">
<tr>
  <td>Transaction<br>Date</td>
  <td>Reported<br>DateTime</td>
  <td>Company</td>
  <td>Symbol</td>
  <td>Insider<br>Relationship</td>
  <td>Shares<br>Traded</td>
  <td>Average<br>Price</td>
  <td>Total<br>Amount</td>
  <td>Shares<br>Owned</td>
  <td class="no-sort">Filing</td>
</tr>
</thead>
<tbody>
<tr>
  <td class="S">2025-11-07<br>Sale</td>
  <td>2025-11-12<br>6:30 pm</td>
  <td>Apple Inc.</td>
  <td><a class="qLink" href="#">AAPL</a></td>
  <td><a href="/insider-trading/1631982.htm">KONDO CHRIS</a><br><span class="pos">Principal Accounting Officer</span></td>
  <td>3,752</td>
  <td>$271.23</td>
  <td>$1,017,655</td>
  <td>15,098<br><span class="ownership">(Direct)</span></td>
  <td><a rel="nofollow" href="/filings/320193/0001631982-25-000011.htm">View</a></td>
</tr>
<tr>
  <td class="S">2025-10-16<br>Sale</td>
  <td>2025-10-17<br>6:31 pm</td>
  <td>Apple Inc.</td>
  <td><a class="qLink" href="#">AAPL</a></td>
  <td><a href="/insider-trading/2050912.htm">Parekh Kevan</a><br><span class="pos">Senior Vice President, CFO</span></td>
  <td>4,199</td>
  <td>$247.39</td>
  <td>$1,038,787</td>
  <td>40,840<br><span class="ownership">(Direct)</span></td>
  <td><a rel="nofollow" href="/filings/320193/0002050912-25-000008.htm">View</a></td>
</tr>
<tr>
  <td class="S">2025-10-02<br>Sale</td>
  <td>2025-10-03<br>6:33 pm</td>
  <td>Apple Inc.</td>
  <td><a class="qLink" href="#">AAPL</a></td>
  <td><a href="/insider-trading/1214156.htm">COOK TIMOTHY D</a><br><span class="pos">Chief Executive Officer</span></td>
  <td>129,963</td>
  <td>$256.81</td>
  <td>$33,375,723</td>
  <td>3,280,295<br><span class="ownership">(Direct)</span></td>
  <td><a rel="nofollow" href="/filings/320193/0001214156-25-000011.htm">View</a></td>
</tr>
<tr>
  <td class="P">2025-09-15<br>Purchase</td>
  <td>2025-09-17<br>6:30 pm</td>
  <td>Apple Inc.</td>
  <td><a class="qLink" href="#">AAPL</a></td>
  <td><a href="/insider-trading/9999999.htm">Pelosi Nancy</a><br><span class="pos">Director</span></td>
  <td>10,000</td>
  <td>$220.00</td>
  <td>$2,200,000</td>
  <td>50,000<br><span class="ownership">(Direct)</span></td>
  <td><a rel="nofollow" href="/filings/320193/0009999999-25-000001.htm">View</a></td>
</tr>
</tbody>
</table>
</div>
</body>
</html>
"""

# -----------------------------------------------------------------------
# openinsider.com sample HTML
# -----------------------------------------------------------------------

OPENINSIDER_HTML = """
<html>
<body>
<table class="tinytable">
<tr>
  <th>Filing Date</th>
  <th>Trade Date</th>
  <th>Ticker</th>
  <th>Company Name</th>
  <th>Insider Name</th>
  <th>Title</th>
  <th>Trade Type</th>
  <th>Qty</th>
  <th>Price</th>
  <th>Value</th>
  <th>Shares Owned After</th>
</tr>
<tr>
  <td>2025-11-17</td>
  <td>2025-11-15</td>
  <td>AAPL</td>
  <td>Apple Inc</td>
  <td>Cook Timothy D</td>
  <td>CEO</td>
  <td>S - Sale</td>
  <td>100,000</td>
  <td>$185.50</td>
  <td>$18,550,000</td>
  <td>3,280,000</td>
</tr>
<tr>
  <td>2025-11-14</td>
  <td>2025-11-12</td>
  <td>MSFT</td>
  <td>Microsoft Corp</td>
  <td>Nadella Satya</td>
  <td>CEO</td>
  <td>S - Sale</td>
  <td>200,000</td>
  <td>$420.00</td>
  <td>$84,000,000</td>
  <td>5,000,000</td>
</tr>
<tr>
  <td>2025-11-10</td>
  <td>2025-11-08</td>
  <td>TSLA</td>
  <td>Tesla Inc</td>
  <td>Tuberville Tommy</td>
  <td>Director</td>
  <td>P - Purchase</td>
  <td>5,000</td>
  <td>$310.00</td>
  <td>$1,550,000</td>
  <td>15,000</td>
</tr>
</table>
</body>
</html>
"""

# -----------------------------------------------------------------------
# EDGAR CIK resolution HTML
# -----------------------------------------------------------------------

EDGAR_CIK_HTML = """
<html>
<body>
<div class="companyInfo">
  <span class="companyName">APPLE INC
    <a href="/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000320193&amp;type=4">
      CIK=0000320193
    </a>
  </span>
</div>
</body>
</html>
"""

EDGAR_CIK_NOT_FOUND_HTML = """
<html>
<body>
<div>No matching companies found.</div>
</body>
</html>
"""
