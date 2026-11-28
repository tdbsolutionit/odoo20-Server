# Omnia CAD database discovery

## CAD connection on port 8070

Use `http://localhost:8070` and database `odoo20_server` for this instance.
On a separate CAD workstation, replace localhost with the Odoo server address.

`server_wide_modules = base,web,rpc,tdb_custom_plm` makes the discovery route
available before login. `POST /xmlrpc/db` methods `list` and `list_db` expose
only the explicitly configured `db_name` entries allowed by `dbfilter`, even
when `list_db = False`. General database management stays disabled. If no
database is configured, discovery follows Odoo's normal listing policy.

After changing this Python controller or configuration, restart with
`start_odoo.ps1`; no module upgrade is required for this connection fix.

## Odoo 20 preview compatibility

The CAD reviewer picker also adapts `res.users.read` and `search_read` RPC
responses: binary avatars are returned as legacy base64 strings instead of
Odoo 20 binary dictionaries. Previously flattened columns such as
`avatar_128.size` are resolved through a normal read of the underlying binary,
preserving field and record access checks. Web reads keep the native Odoo 20
format. Restart Odoo and reopen the CAD picker after applying this Python fix.

For CAD (`odooPLM` context), the user list exported through
`koo_fields_view_get` contains only Name, Login and Company. This avoids
administrative/image widgets in the legacy selection grid. A compatibility
`name_get` method resolves selected user IDs to display names. The regular web
user view is unchanged, and reviewer role domains and ORM access checks still
apply. The CAD picker tests include an engineer finding a permitted reviewer
and saving both reviewer IDs through `product.product.createFromProps`.

The `models/ir_attachment.py` extension fixes thumbnail encoding without editing
the upstream `plm` module. HTML previews encode Odoo 20 binary values with
`to_base64()`. Legacy RPC reads return base64 strings for `preview` and
`preview_related`; web reads retain Odoo's native binary dictionaries. Documents
without a thumbnail retain PLM's placeholder image.

Install or upgrade `tdb_custom_plm` in the target database and restart its Odoo
server to activate these model overrides. This server uses `odoo20_server` on
port 8070; paths and database names in the older discovery notes below refer to
the separate development instance.

## Historical notes for the separate development instance

The notes below describe the older `odoo_dev` instance, not the current 8070
configuration or the current discovery implementation.

`POST /xmlrpc/db` accepts XML-RPC methods `list` and `list_db` and always
returns `['odoo_dev']`. Other methods return an XML-RPC fault. This endpoint
does not query PostgreSQL or provide database administration operations.

Add the parent `tdb_custom_addons` directory to `addons_path`. Install this
module on `odoo_dev` and add it to the existing server-wide module list so
the route is available before a database has been selected, for example:

```ini
server_wide_modules = base,web,rpc,tdb_custom_plm
```

Alternatively, use `--load=base,web,rpc,tdb_custom_plm`. Preserve any other
server-wide modules required by your deployment, then restart Odoo.

For this workspace, append `C:\Odoo20.0\odoo\tdb_custom_addons` to the
existing `addons_path`. Neither `odoo.conf` nor `odoo_8070.conf` currently
includes this directory. Select the configuration used by your actual
`odoo_dev` server; `odoo_8070.conf` targets a different database.

Loading this module server-wide makes database discovery available even
without a selected database. Installing it alone does not ensure discovery
works before login.

## PLM Documents default filter

This module depends on `plm` and updates `plm.plm_action_document_form` so
Documents opens without the **Must Update from CAD** filter selected.
The filter remains available for manual selection, and the `odooPLM`
context is preserved.

After deploying these files, restart Odoo and install or upgrade
`tdb_custom_plm` in the target database through Apps. Reload the browser
and reopen PLM > Documents to apply the updated action.

Only `/xmlrpc/db` is provided here. `/xmlrpc/2/db` and `/jsonrpc` are not
extended. The fixed name is returned even if `odoo_dev` does not exist or
`list_db` is disabled; it does not verify availability or grant login access.

Verify against the running server (adjust the URL/port):

```python
from xmlrpc.client import ServerProxy

db = ServerProxy('http://localhost:8069/xmlrpc/db')
assert db.list() == ['odoo_dev']
assert db.list_db() == ['odoo_dev']
```

Use `odoo_dev` as the database in Omnia CAD. Authentication and model calls
continue through Odoo's existing RPC endpoints.


## Level-based review and redo workflow (20.0.2.0.0)

Business states remain Draft -> Review (`confirmed`) -> Released -> Obsolete
(`obsoleted`). Omnia's technical `undermodify` state remains for revision chains.

### Roles and submission

- **LV1:** PLM Integration User without either higher custom role.
- **LV2:** OdooPLM - Reviewer (`group_review_manager`).
- **LV3:** OdooPLM - Confirm and Release (`group_review_admin`).
- Highest assigned level wins. A Settings administrator is not automatically an
  LV3 unless assigned the PLM LV3 business role.

There is one **Reviewer** field. LV1 can select LV2 or LV3; LV2 can select LV3.
The reviewer must be active, internal and able to write the record in its
company. These rules are enforced by the server, not just the chooser domain.

1. In Draft, click **Submit Review** on the browser or CAD workflow form, choose
   an eligible higher-level reviewer, and confirm. A saved Reviewer is preselected;
   canceling leaves the record unchanged. LV1 can choose LV2/LV3; LV2 chooses LV3.
   Existing low-level CAD/API `action_confirm` calls remain compatible with saved
   reviewer assignments; the form button uses the new chooser action.
2. A review activity is assigned to that reviewer and the record enters Review.
3. LV2 uses **Approve and Submit**, chooses a Level 3 reviewer, and confirms
   once to record approval and immediately assign the final review to LV3.
   The record stays in Review. Existing onward-submission tasks can still use **Submit to Level 3**
   to select an LV3 reviewer and explicitly request the next review.
4. An assigned LV3 approves and releases. An LV1 request sent directly to LV3
   does not require an LV2 decision first.
5. LV3 can Confirm a Draft record directly without selecting any reviewer,
   including work they have corrected themselves. This is an explicit policy
   exception and is recorded as `admin_direct_confirm` in Chatter/history.

The existing Omnia Component workflow still traverses its related records.
Reviewer defaults can be inherited from a parent BoM when the Draft Part has no
assignment. A Document can obtain its reviewer from its linked Part. Explicit
assignments are preserved and conflicting defaults block submission. Sharing
and recursive submission policy remains separate from level authorization.

### Rejection and redo

The assigned reviewer uses **Reject / Assign Redo**, supplies a mandatory reason
and chooses themselves or a lower-level PLM user. Only that selected record
returns to Draft; child records keep their current states.

The active redo assignee can edit the Draft content. Other users, including
higher-level users, cannot silently edit or complete that assigned work.

- A worker can delegate redo strictly downward, with instructions and deadline.
- Delegation creates a child task and leaves the assigning worker's parent task
  open. Completing the child returns responsibility to the assigning worker.
- Example: LV3 -> LV2 -> LV1. LV1 completion returns to LV2 for verification;
  LV2 completion returns the work to LV3 as a review request.
- LV2 who redoes their own rejected work must submit it to LV3; they do not
  approve their own correction.
- LV3 who redoes their own work may use **Confirm Redo and Release**, following
  the requested administrator exception.

Each submission, approval, rejection, delegation, return and admin confirmation
is retained in structured JSON history and Chatter. The active redo stack keeps
parent task IDs and assigned users, so it is not just an activity description.
Activities alone never grant approval. Mark Done, activity reassignment,
archiving and deletion are blocked for managed tasks outside workflow actions.
The Omnia Force Activity Validation role does not bypass these checks.

### Activity Validation / CAD

`activity_validation` is an explicit dependency and was already installed on
`odoo20_server`; no additional Omnia module is required. Three standard
`mail.activity` types serve Review, Submit to Level 3 and Redo tasks. These do
not reuse the generic ECR/ECO actions that automatically finish child activities.

Activities appear in the Odoo activity inbox and on the record. Open the task
and choose **Open Review / Redo** to perform the authorized workflow action.
The CAD `get_user_activities` response also includes these tasks with a
**Review / Redo** button; its generic Progress/Done/Cancel actions are disabled
for managed tasks. A CAD version must support opening the returned form action
for these assignment dialogs; the same actions are available on Odoo.

### Upgrade and existing reviews

This version adds fields, activity types, views and a wizard: it requires a
module upgrade, restart, and reconnecting CAD so it reloads the new field/view
metadata. From `C:\Odoo20.0\odoo20-Server` after stopping the running instance:

```powershell
.\.venv\Scripts\python.exe odoo-bin -c odoo.conf -d odoo20_server -u tdb_custom_plm --stop-after-init --no-http
.\start_odoo.ps1
```

Use the launcher environment fix for `PYTZ_TZDATADIR` when running the upgrade
manually, or clear the invalid override in that PowerShell session first.

Old Manager/Admin columns and historical decisions are retained, but no longer
appear as two mandatory reviewer inputs. An eligible old assignment may serve
as an initial Reviewer default. LV3 can filter Parts or Documents by **Review Needs Recovery**, select records,
and use **Action > Recover Unassigned Review**. Choose an LV1/LV2 request owner,
a higher-level reviewer, and a reason. Recovery keeps the records in Review,
creates review activities and preserves historical decisions. Valid active
reviews cannot be replaced by this action; nothing is automatically released.
Existing Released records retain their state and audit history.

### Live PLM views

Live browser updates are included: backend invalidation signals contain
only model names and are sent to PLM users' private bus channels. The browser
reloads clean PLM forms/lists/kanban views, batches bursts, and retries missed
updates every 15 seconds. Dirty/new/inline-edit/quick-create views are deferred.
The native CAD dialogs themselves still follow the CAD application's refresh
behavior. Code changes still require the normal restart/upgrade process.

### Verification

Run against an isolated database, not the live database:

```powershell
.\.venv\Scripts\python.exe odoo-bin -c odoo.conf -d <test_database> -u tdb_custom_plm --http-port=0 --test-enable --test-tags tdb_plm_review,tdb_plm_cad_users,tdb_plm_live --stop-after-init --no-http
node --test custom_addons/tdb_custom_plm/tests/live_refresh_queue.test.mjs
```

Tests cover upward review, explicit LV2 forwarding, direct LV3 confirmation,
self-redo rules, chained delegation and returns, active-worker editing,
activity bypass attempts, forged context/data, CAD reviewer selection and inbox,
legacy reset, revision isolation and private live-update signals.

### Task notifications

Assignments and returned delegated work send a durable Odoo inbox notification
and a live alert, including when the recipient prefers email. Returning work
reactivates the existing parent task without creating a duplicate. Email is
optional: enable system parameter `tdb_plm.email_notifications` only after SMTP
is configured. Existing failed emails are retained for diagnosis.

Review, forwarding, and redo tasks also appear under **PLM > Activities**,
alongside ECR/ECO tasks. **My PLM Activities** is applied by default to show
your open assignments; remove it to browse other activities. Use **Review / Redo**
and **Open Review / Redo** to act. Returned delegated work updates the existing
parent task summary to request verification. The Activities list refreshes live.

Clicking a row in **PLM > Activities** opens its linked Part or document directly
in the full PLM form. The separate Open Review / Redo button remains available.
This navigation is specific to the PLM Activities list; other activity lists
retain their normal behavior.

PLM Component forms show the `product.template` Chatter thread used by the
review workflow, including existing activities and decision messages. Ordinary
product variant forms outside PLM retain their variant thread. No messages or
activities are copied, deleted or moved; live reload also refreshes the workflow
thread displayed in the Component form.

The clock dropdown combines PLM review/redo and ECR/ECO activities into one
**PLM Activities** group. Its Late/Today/Future links open the PLM Activities
action filtered to the corresponding assigned tasks. Unrelated activities
retain their standard model groups.

### Recursive rejection, submission conflicts, and release

Reject / Assign Redo asks only for an assignee, reason and deadline. It returns
the selected Part or document and its reviewed descendants to Draft
with redo tasks. No child selection or scope-preview fields are shown. Each
affected record retains its own history. Released references and existing Draft
or redo tasks are preserved. Conflicting reviewers and checked-out files block
the operation before any states are changed. Legacy overlapping assembly reviews
still require coordination before a shared revision can be unlocked.

Propagation follows OdooPLM's direction: Part actions include eligible linked
files, while document actions do not change linked Parts. Document rejection
follows its CAD child structure without walking back to the corresponding Part.

Submit Review is blocked when shared Parts or CAD references in the scope are
already in Review. The error lists their codes, names and assigned reviewers.
The check runs before opening the chooser and again on confirmation, including
native Part/document confirmation and direct state writes. Internal traversal
of a validated submission scope is distinguished from a second submission, so
submitting the first assembly does not block itself halfway through. Existing
review tasks are never reassigned or duplicated by a blocked submission.
The upgrade removes the previous selection widgets before deleting their
transient fields; it does not reset existing Part/document review data.

LV3 Approve/Release is recursive from both Part and file forms: it releases
linked files, drawings and child Parts in the same operation. There is no
bottom-up readiness gate requiring children to be Released before the parent.
Releasing a reviewed Part processes descendants in Review and skips Draft files
or Parts, including their existing redo tasks. Thus a reverted document remains
Draft when its Part is released. Direct LV3 confirmation of a Draft root retains
its existing ability to confirm eligible Draft descendants. Every affected record enforces its own reviewer
permissions, checked-in requirement, and active-redo restrictions. The scope is
checked before any release changes; failures roll back the operation. Already
Released records remain unchanged. Cycle detection prevents infinite recursion.
Normal Parts without engineering codes are outside this engineering workflow.

### CAD Part revision compatibility

The CAD `product.product.NewRevision` entry point uses OdooPLM's template
revision-copy routine and preserves its `(new Part ID, revision index)` result.
This avoids legacy variant writes that inject a placeholder description into
the Released source while moving it to Under Modification. The source content
stays frozen; the new revision starts in Draft without copied review decisions,
tasks or document links. The CAD revision process reconnects the revised files.
The latest Part revision must be Released and its linked files checked in.
Document revision accepts both the explicit document ID used by legacy RPC
and the singleton call used by combined CAD Part/document revision.
