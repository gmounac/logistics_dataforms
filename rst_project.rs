use chrono::NaiveDate; // 0.4.45
use std::fmt::Display;
use std::{fmt,str::FromStr}; // report.rs
use serde::{Deserialize,Serialize}; // report.rs
// use rust_decimal::Decimal; // report.rs
use std::collections::BTreeMap; // action.rs
use thiserror::Error; // 2.0.20; // error.rs
use axum

// error.rs

#[derive(Error, Debug, Clone, PartialEq, Eq)]
pub enum ReportError {
    #[error("invalid month: {0} (expected 1-12)")]
    InvalidMonth(u32),
    #[error("report {0} not found")]
    NotFound(ReportId),
    #[error("start_date must not be after end_date")]
    InvalidDateRange,
}

#[derive(Error,Debug,Clone,PartialEq,Eq)]
pub enum AppError {
    #[error("invalid {field}: {value}")]
    InvalidValue { field: &'static str, value: String },

}
/// report.rs
// ------------------------------
// ReportType Struct
// The different kind of invoicing based on our system.
// For example:
//monthly are services which are for client not involving ship
// associated service which are invoiced from the start of the month to the end of a month
// SI are all services not relating to a vessel unloading on site which it's range is not month based.
// STO are based on the range a vessel berthed at the port
// OSS are based on h=the range a vessel berthed at the port which are invoiced to the shipping line instead
// of the client. However, the sub-type CCCS OSS is monthly based and are for services occuring at the cold store.
#[derive(Debug, Clone, Copy,PartialEq, Eq,Serialize,Deserialize)]
pub enum ReportType {
    MONTHLY,
    SI,
    STO,
    OSS,
}



impl ReportType {


    pub const ALL: [ReportType;4] = [Self::Monthly,Self::SI,Self::STO,Self::OSS];

    pub fn as_str(self) -> &'static str {
        match Self {
            Self::MONTHLY => "MONTHLY",
            Self::SI => "SI",
            Self::STO => "STO",
            Self::OSS => "OSS",
        }
    }
}

impl Display for ReportType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {

        f.write_str(self.as_str())
    }
}

impl FromStr for ReportType {
    type Err = AppError;
    fn from_str(s:&str) -> Result<Self,Self::Err>{
        Self::ALL
            .into_iter()
            .find(|t|t.as_str()==s)
            .ok_or_else(|| AppError::InvalidValue{field: "report_type",value:s.to_owned()})
    }
}

// ReportStatus
// ----------------------
// This is the state of each report.
// It starts at Pending (the default), the when the supporting evidence is ready and/or the
// data has been processed  but not sent it becomes Ready. This may due to an issue or query which
// prevents the closure of the report. The when reported it becomes a SentForApproval.
// The case closed are for reports which may have been added by mistake and there are no service
// or for recurring monthy service which for a given period there were no service.

#[derive(Default, Debug, Clone, Copy, PartialEq, Eq,Serialize,Deserialize)]
pub enum ReportStatus {
    Ready,
    #[default]
    Pending,
    Closed,
    SentForApproval,
}

impl ReportStatus {

    pub const ALL: [ReportStatus,4] =
    [Self::Ready,Self::Pending,Self::Closed,Self::SentForApproval]

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ready => "Ready",
            Self::Pending => "Pending",
            Self::Closed => "Closed",
            Self::SentForApproval => "SentForApproval",
        }
    }
}

impl Display for ReportStatus {
    fn fmt(&self,f:&mut std::fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for ReportStatus {
    type Err = AppError;

    fn from_str(s:&str) -> Result<Self,Self::Error> {
        Self::ALL
            .into_iter()
            .find(|t| t.as_str() == s)
            .ok_or_else(||AppError::InvalidValue {field: "status", value = s.to_owned()})
    }
}

// A stored report as returned by the API

#[derive(Debug, Clone, PartialEq,Serialize)]
pub struct Report {
    pub id:i64,
    pub year: i32,
    pub month: u32,
    pub invoice_number: Option<String>,
    pub report_type: ReportType,
    pub sub_type: String,      // for now
    pub vessel_client: String, // for now
    pub customer: String,
    pub start_date: Option<NaiveDate>,
    pub end_date: Option<NaiveDate>,
    pub remarks: Option<String>,
    pub status: ReportStatus,
    /// Kept as f64 for now. For exact money arithmetic switch to
    /// `rust_decimal::Decimal` stored as TEXT, or integer cents.
    pub invoice_amount: Option<f64>,
    pub created_at: String,
    pub updated_at: String,
}


// POST and PUT

#[derive(Debug,Clone,PartialEq,Deserialize)]
pub struct ReportInput {
    pub year:i32,
    pub month:u32,
    pub invoice_number:Option<String>,
    pub report_type:ReportType,
    pub sub_type:String,
    pub vessel_client: String,
    pub customer: String,
    pub start_date:Option<NaiveDate>,
    pub end_date:Option<NaiveDate>,
    pub remarks: Option<String>,
    #[serde(default)]
    pub status: ReportStatus,
    pub invoice_amount: Option<f64>,
}

impl ReportInput {

    pub fn validate(&self) -> Result<(),ReportError> {
        if !(1..=12).contains(&self.month) {
            return Err(ReportError::InvalidMonth(self.month));

        }
        if let Some(start),Some(end) = (self.start_date,self.end_date) {
            if start > end {
                return Err(ReportError::InvalidDateRange);
            }
        }
        Ok(())
    }
}









impl Report {
    pub fn new(
        month: YearMonth,
        report_type: ReportType,
        sub_type: impl Into<String>,
        vessel_client: impl Into<String>,
        customer: impl Into<String>,
        start_date: Option<NaiveDate>,
        end_date: Option<NaiveDate>,
    ) -> Self {
        Self {
            month,
            invoice_number: None,
            report_type,
            sub_type: sub_type.into(),
            vessel_client: vessel_client.into(),
            customer: customer.into(),
            start_date,
            end_date,
            remarks: None,
            status: ReportStatus::default(),
            invoice_amount: None,
        }
    }
    pub fn with_invoice(mut self, number: impl Into<String>, amount: f64) -> Self {
        self.invoice_number = Some(number.into());
        self.invoice_amount = Some(amount);
        self.status = ReportStatus::SentForApproval;
        self
    }

    pub fn with_remarks(mut self, remarks: impl Into<String>) -> Self {
        self.remarks = Some(remarks.into());
        self
    }
}

impl Display for Report {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let fmt_date = |d: Option<NaiveDate>| {
            d.map(|d| d.format("%d/%m/%Y").to_string())
                .unwrap_or_else(|| "-".into())
        };
        write!(
            f,
            "{month} | {ty:<7} | {sub:<25} | {cust:<6} | {start} - {end} | {inv:<12} | {amt:>12} | {status:?}",
            month = self.month,
            ty = self.report_type,
            sub = self.sub_type,
            cust = self.customer,
            start = fmt_date(self.start_date),
            end = fmt_date(self.end_date),
            inv = self.invoice_number.as_deref().unwrap_or("-"),
            amt = self
                .invoice_amount
                .map(|a| format!("{a:.2}"))
                .unwrap_or_else(|| "-".into()),
            status = self.status,
        )
    }
}

/// year.rs

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct YearMonth {
    year: i32,
    month: u32,
}

impl YearMonth {
    pub fn new(year: i32, month: u32) -> Result<Self, ReportError> {
        (1..=12)
            .contains(&month)
            .then_some(Self { year, month })
            .ok_or(ReportError::InvalidMonth(month))
    }

    pub fn from_date(d: NaiveDate) -> Self {
        use chrono::Datelike;
        Self {
            year: d.year(),
            month: d.month(),
        }
    }
}

impl Display for YearMonth {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}-{}", self.month, self.year)
    }
}
/// action.rs
pub type ReportId = u32;

#[derive(Debug, Default)]
pub struct ReportStore {
    next_id: ReportId,
    reports: BTreeMap<ReportId, Report>,
}

impl ReportStore {
    pub fn new() -> Self {
        Self {
            next_id: 1,
            reports: BTreeMap::new(),
        }
    }

    /// Create: store a report and return it;s ID
    pub fn create(&mut self, report: Report) -> ReportId {
        let id = self.next_id;
        self.next_id += 1;
        self.reports.insert(id, report);
        id
    }

    // Update
    pub fn update(
        &mut self,
        id: ReportId,
        f: impl FnOnce(&mut Report),
    ) -> Result<&Report, ReportError> {
        let report = self.reports.get_mut(&id).ok_or(ReportError::NotFound(id))?;
        f(report);
        Ok(report)
    }

    // Read

    pub fn list(&self) -> impl Iterator<Item = (ReportId, &Report)> {
        self.reports.iter().map(|(id, r)| (*id, r))
    }

    /// Read : printing
    pub fn display_all(&self) {
        if self.reports.is_empty() {
            println!("no reports");
            return;
        }
        for (id, report) in self.list() {
            println!("#{id:<3} {report}");
        }
    }

    /// Delete
    pub fn delete(&mut self, id: ReportId) -> Result<Report, ReportError> {
        self.reports.remove(&id).ok_or(ReportError::NotFound(id))
    }

    pub fn get(&self, id: ReportId) -> Option<&Report> {
        self.reports.get(&id)
    }
}

/// main.rs
fn main() -> Result<(), ReportError> {
    let mut store = ReportStore::new();

    // Create
    let id = store.create(
        Report::new(
            YearMonth::new(2026, 7)?,
            ReportType::Monthly,
            "BIN DISPATCH TO IOT",
            "IOT",
            "IOT",
            NaiveDate::from_ymd_opt(2026, 7, 1),
            NaiveDate::from_ymd_opt(2026, 7, 31),
        )
        .with_invoice("SI26/000441", 28_303.06),
    );

    // Update

    store.update(id, |r| {
        r.status = ReportStatus::Ready;
        r.remarks = Some("Awaiting PO number".into());
    })?;

    // Display all
    store.display_all();

    // Delete
    let removed = store.delete(id)?;
    println!(
        "deleted #{id}: {} {} {}",
        removed.customer, removed.sub_type, removed.month
    );

    // Error Path
    assert_eq!(store.delete(id), Err(ReportError::NotFound(id)));
    Ok(())
}
