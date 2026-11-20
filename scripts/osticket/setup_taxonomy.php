<?php
/**
 * Create the Guidewire taxonomy in an osTicket install:
 *   - one Help Topic per module (what the requester picks when opening a ticket)
 *   - an "Issue Category" custom list plus an agent-only dropdown on the Ticket Details form
 *
 * Idempotent: anything that already exists is left alone. Uses osTicket's own model
 * classes (not raw SQL), so sort order, caches and the ticket custom-data view stay consistent.
 *
 * Usage (normally via scripts/osticket_setup_taxonomy.py, which feeds the JSON from backend/taxonomy.py):
 *   php setup_taxonomy.php <osticket-dir> [--dry-run] < taxonomy.json
 *   taxonomy.json: {"modules": [...], "categories": [...], "category_label": "Issue Category"}
 */
if (PHP_SAPI !== 'cli') exit(1);

$root = rtrim($argv[1] ?? '', '/');
$dry  = in_array('--dry-run', $argv, true);
if (!$root || !is_file("$root/bootstrap.php")) {
    fwrite(STDERR, "Usage: php setup_taxonomy.php <osticket-dir> [--dry-run] < taxonomy.json\n");
    exit(2);
}
$tax = json_decode(stream_get_contents(STDIN), true);
if (!is_array($tax) || empty($tax['modules']) || empty($tax['categories'])) {
    fwrite(STDERR, "taxonomy JSON on stdin must contain non-empty 'modules' and 'categories'\n");
    exit(2);
}
$label = $tax['category_label'] ?? 'Issue Category';

chdir($root);
require_once 'bootstrap.php';
require_once CLI_DIR . 'cli.inc.php';
Bootstrap::loadConfig();
Bootstrap::defineTables(TABLE_PREFIX);
Bootstrap::loadCode();
Bootstrap::connect();
if (!($ost = osTicket::start()) || !($cfg = $ost->getConfig())) {
    fwrite(STDERR, "Unable to start osTicket (check include/ost-config.php)\n");
    exit(1);
}
$say = function ($m) use ($dry) { echo ($dry ? '[dry-run] ' : '') . $m . "\n"; };

// ---- 1. Help topics = modules -------------------------------------------------
$deptId = $cfg->getDefaultDeptId();
foreach ($tax['modules'] as $name) {
    if (Topic::getIdByName($name, 0)) { $say("topic exists:  $name"); continue; }
    $say("topic create: $name");
    if ($dry) continue;
    $errors = array();
    $topic = Topic::create(array());
    $ok = $topic->update(array(
        'topic' => $name, 'topic_pid' => 0, 'dept_id' => $deptId, 'priority_id' => 0,
        'ispublic' => 1, 'status' => 'active',
        'notes' => 'Guidewire module (matches the module taxonomy used by the AI app)',
    ), $errors);
    if (!$ok) { fwrite(STDERR, "FAILED topic $name: " . json_encode($errors) . "\n"); exit(1); }
}

// ---- 2. Category custom list ----------------------------------------------------
$list = DynamicList::objects()->filter(array('name' => $label))->first();
if ($list) {
    $say("list exists:  $label");
} else {
    $say("list create: $label");
    if (!$dry) {
        $errors = array();
        $list = DynamicList::create(array(
            'name' => $label, 'name_plural' => $label . ' values', 'sort_mode' => 'Alpha',
            'masks' => 13, 'notes' => 'Guidewire issue categories (managed by scripts/osticket_setup_taxonomy.py)',
        ), $errors, false);
        $list->save();
    }
}
if ($list) {
    foreach ($tax['categories'] as $i => $value) {
        if ($list->getItem($value)) { $say("  item exists:  $value"); continue; }
        $say("  item create: $value");
        if ($dry) continue;
        $errors = array();
        $item = $list->addItem(array('value' => $value, 'sort' => $i + 1), $errors);
        if (!$item || !$item->save()) { fwrite(STDERR, "FAILED item $value\n"); exit(1); }
    }
}

// ---- 3. Dropdown field on the Ticket Details form (agent-only) ------------------
// getInstance() is a stand-in without a real id; look up the persisted Ticket Details form.
$form = TicketForm::objects()->first();
if (!$form || !$form->get('id')) { fwrite(STDERR, "Ticket Details form not found\n"); exit(1); }
$fieldName = strtolower(preg_replace('/[^a-z0-9]+/i', '_', $label));
$existing = DynamicFormField::objects()
    ->filter(array('form_id' => $form->get('id'), 'name' => $fieldName))->first();
if ($existing) {
    $say("field exists: $fieldName on Ticket Details");
} else {
    $say("field create: $fieldName on Ticket Details (agent-only dropdown)");
    if (!$dry) {
        // enabled | agent view | agent edit. Requesters choose the module topic; agents/AI set the category.
        $flags = DynamicFormField::FLAG_ENABLED | DynamicFormField::FLAG_AGENT_VIEW | DynamicFormField::FLAG_AGENT_EDIT;
        $max = 0;
        foreach ($form->getFields() as $f) $max = max($max, (int) $f->get('sort'));
        $field = DynamicFormField::create(array(
            'form_id' => $form->get('id'), 'type' => 'list-' . $list->getId(),
            'label' => $label, 'name' => $fieldName, 'sort' => $max + 1, 'flags' => $flags,
            'configuration' => array('multiselect' => false, 'widget' => 'dropdown', 'prompt' => 'Select a category'),
        ));
        if (!$field->save()) { fwrite(STDERR, "FAILED to add field $fieldName\n"); exit(1); }
    }
}

// ---- 4. Make sure the ticket custom-data table has the column --------------------
// ost_ticket__cdata is a materialized table; it must be rebuilt after a field is added, and only
// after osTicket's model cache is flushed (otherwise it is rebuilt from the stale field list).
if (!$dry) {
    $has = db_num_rows(db_query("SHOW COLUMNS FROM `" . TICKET_CDATA_TABLE . "` LIKE '" . db_input($fieldName, false) . "'"));
    if (!$has) {
        $say("rebuild ticket custom-data table (adding column $fieldName)");
        ModelInstanceManager::flushCache();
        TicketForm::dropDynamicDataView(false);
        TicketForm::ensureDynamicDataView(true);
    }
}
$say('done');
