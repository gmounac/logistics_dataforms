# Tasks to do.

Fix the forms

## Gate in Form.
----

* Add the behaviour, when a container gates in full, it should be marked as PTI by default.
  * However, we allow damaged/malfunction to be selected.
* Remove completed from the list of statuses.
* License plate autotype the starting 'S' also automatically store recent values to ease entry.

## Gate Out Form
----

* Remove completed from the list of statuses.
* License plate autotype the starting 'S' also automatically store recent values to ease entry.

## Cleaning
-----

* Ensure to select only containers which are empty.
  * Even if the check box is selected. We cannot wash a full container.
* Ensure that the container is empty before selecting it, and not on PTI.

## Plugin 
----

* Change set point to a dropdown list, which is also editable.
* Make Cargo default to Full. Check if to keep the option 'Unchanged'.

## Plugout
----

* Change the note under the container_number field from "Reefers plugged in for storage" to "Reefers currently plugged in".

## PTI Plugin
----

* Change the set point field to a dropdown list, make it uneditable.
* Backend: Compare set point against the `containers` table to ensure it matches.
* To add unit_manufacturer dropdown to the forms.
* Backend: Compare unit_manufacturer against the `containers` table to ensure it matches.

## PTI Plugout
----

* To change this behaviour for failed containers, `PASS` marks the container as PTI; `RED`, `TBR` or `NA` as NON PTI. and set to Malfunction.
* Need to report on the main view when containers are still plugged in for PTI.

## Temperature
----

* To add None to the remarks field. And place it as the default value.
* Ensure only plugged in containers for Storage are listed.
* Report units not monitored.
* Make the field set point, supply and return nullable.
  * However this is done automatically when selecting the remarks N/A.
  * However, comment is needed when the remarks are not N/A, or a dropdown appears where we can select a reason for the N/A remarks.
    * OFF power
    * Heavy Rainfall
    * Others 
      * which users have to define.
* Flag containers which have been monitored on the same date and time frame.
* 

## Records
-------------

* To use icons instead of buttons for edit and delete when the enable editing check box is selected.
* To make the search containers work without the From and to Date selection.
* To make the From and to Date selection be able to change to select only one date.
* To search by shipping line (Check if shipping line can be joined to the `events` table)

* Can we make the views table filterable?
* For example, the **Gate in/Gate out view**:
  * When filters by date. (not datetime)
  * Cargo by Empty or Full (include Partial in full)
  * PTI Status by PTI and NON PTI.
* Add Unit Manufacturer and Shipping line to the view.
* Remove the Hauler field in view but keep it in the `Copy Table` and `Download CSV` functions.
* Format the datetime in the `Copy Table` and `Download CSV` functions to datetime instead of string.
* For the **Plug in (storage + PTI) view**:
  * Rename the kind field to only Plugin Storage.
  * Remove Supply and Return fields, and the Tare kg.
  * Allow filtering by date and Shipping line status.
* For the **Plug out / PTI Unlug view** :
  * Allow filtering by date and Shipping line status.
  * Make it for storage only, remove PTI related fields.
* Add one views for **PTI** to the Kind field:
  * Combine both plug in and plug out into a single view.
  * Allow filtering by date , Shipping line and PTI status.

----

Find a way to include Third Party for Hauler in both the Gate In and Gate Out forms.
