-- QueueX Stored Procedures
-- Phase 3 + Phase 5 Step 3: Audit trail logging
-- Concurrency-safe token issuing with Queue_History logging

DROP PROCEDURE IF EXISTS issue_token;

DELIMITER $$

CREATE PROCEDURE issue_token(
    IN p_queue_id INT,
    IN p_customer_id INT,
    OUT p_token_id INT,
    OUT p_token_number INT
)
BEGIN
    DECLARE v_last_token INT;
    DECLARE v_is_active BOOLEAN;

    -- If anything fails, roll back the transaction.
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    -- Lock this queue row so two customers cannot
    -- generate the same token number simultaneously.
    SELECT last_token_number, is_active
    INTO v_last_token, v_is_active
    FROM Queue
    WHERE queue_id = p_queue_id
    FOR UPDATE;

    -- Make sure the queue exists and is active.
    IF v_last_token IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Queue does not exist';
    END IF;

    IF v_is_active = FALSE THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Queue is not active';
    END IF;

    -- Generate the next token number.
    SET p_token_number = v_last_token + 1;

    -- Update the queue's last token number.
    UPDATE Queue
    SET last_token_number = p_token_number
    WHERE queue_id = p_queue_id;

    -- Create the token.
    INSERT INTO Token (
        queue_id,
        customer_id,
        token_number,
        status
    )
    VALUES (
        p_queue_id,
        p_customer_id,
        p_token_number,
        'waiting'
    );

    -- Return the generated token ID.
    SET p_token_id = LAST_INSERT_ID();

    -- Log to Queue_History (new token starts as waiting, no previous status)
    INSERT INTO Queue_History (token_id, previous_status, new_status, changed_by)
    VALUES (p_token_id, NULL, 'waiting', NULL);

    COMMIT;
END$$

DELIMITER ;

-- ============================================================
-- call_next_token
-- Finds the oldest waiting token for a counter's queue,
-- marks it called, assigns counter/staff, stamps called_at
-- Logs status change: waiting -> called
-- ============================================================
DROP PROCEDURE IF EXISTS call_next_token;

DELIMITER $$

CREATE PROCEDURE call_next_token(
    IN p_counter_id INT,
    IN p_staff_id INT
)
BEGIN
    DECLARE v_token_id INT;
    DECLARE v_queue_id INT;
    DECLARE v_counter_queue_id INT;
    DECLARE v_is_active BOOLEAN;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    -- Get the counter's assigned queue and verify counter is active
    SELECT queue_id, is_active
    INTO v_counter_queue_id, v_is_active
    FROM Counter
    WHERE counter_id = p_counter_id
    FOR UPDATE;

    IF v_counter_queue_id IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Counter does not exist';
    END IF;

    IF v_is_active = FALSE THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Counter is not active';
    END IF;

    -- Find the oldest waiting token for this queue
    SELECT token_id, queue_id
    INTO v_token_id, v_queue_id
    FROM Token
    WHERE queue_id = v_counter_queue_id
      AND status = 'waiting'
    ORDER BY token_number ASC
    LIMIT 1
    FOR UPDATE;

    IF v_token_id IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'No waiting tokens in this queue';
    END IF;

    -- Update the token: status -> called, assign counter/staff, stamp called_at
    UPDATE Token
    SET status = 'called',
        counter_id = p_counter_id,
        staff_id = p_staff_id,
        called_at = CURRENT_TIMESTAMP
    WHERE token_id = v_token_id;

    -- Log to Queue_History: waiting -> called
    INSERT INTO Queue_History (token_id, previous_status, new_status, changed_by)
    VALUES (v_token_id, 'waiting', 'called', p_staff_id);

    COMMIT;

    -- Return the called token details
    SELECT v_token_id AS token_id, v_queue_id AS queue_id;
END$$

DELIMITER ;

-- ============================================================
-- complete_token
-- Marks a token as completed, stamps completed_at
-- Logs status change: called/serving -> completed
-- ============================================================
DROP PROCEDURE IF EXISTS complete_token;

DELIMITER $$

CREATE PROCEDURE complete_token(
    IN p_token_id INT
)
BEGIN
    DECLARE v_status VARCHAR(20);
    DECLARE v_staff_id INT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    -- Lock the token row and check current status
    SELECT status, staff_id
    INTO v_status, v_staff_id
    FROM Token
    WHERE token_id = p_token_id
    FOR UPDATE;

    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Token does not exist';
    END IF;

    IF v_status NOT IN ('called', 'serving') THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Token must be in called or serving status to complete';
    END IF;

    -- Update to completed
    UPDATE Token
    SET status = 'completed',
        completed_at = CURRENT_TIMESTAMP
    WHERE token_id = p_token_id;

    -- Log to Queue_History: previous_status -> completed
    INSERT INTO Queue_History (token_id, previous_status, new_status, changed_by)
    VALUES (p_token_id, v_status, 'completed', v_staff_id);

    COMMIT;
END$$

DELIMITER ;

-- ============================================================
-- mark_no_show
-- Marks a called token as no_show if past the no-show window
-- Logs status change: called -> no_show
-- ============================================================
DROP PROCEDURE IF EXISTS mark_no_show;

DELIMITER $$

CREATE PROCEDURE mark_no_show(
    IN p_token_id INT,
    IN p_window_minutes INT
)
BEGIN
    DECLARE v_status VARCHAR(20);
    DECLARE v_called_at DATETIME;
    DECLARE v_staff_id INT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    -- Lock the token row and check status + called_at
    SELECT status, called_at, staff_id
    INTO v_status, v_called_at, v_staff_id
    FROM Token
    WHERE token_id = p_token_id
    FOR UPDATE;

    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Token does not exist';
    END IF;

    IF v_status != 'called' THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Only called tokens can be marked no_show';
    END IF;

    IF v_called_at IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Token has no called_at timestamp';
    END IF;

    -- Check if no-show window has elapsed
    IF TIMESTAMPDIFF(MINUTE, v_called_at, CURRENT_TIMESTAMP) < p_window_minutes THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'No-show window has not elapsed yet';
    END IF;

    -- Update to no_show
    UPDATE Token
    SET status = 'no_show'
    WHERE token_id = p_token_id;

    -- Log to Queue_History: called -> no_show
    INSERT INTO Queue_History (token_id, previous_status, new_status, changed_by)
    VALUES (p_token_id, 'called', 'no_show', v_staff_id);

    COMMIT;
END$$

DELIMITER ;

-- ============================================================
-- serve_token (optional helper)
-- Marks a called token as serving (staff starts serving)
-- Logs status change: called -> serving
-- ============================================================
DROP PROCEDURE IF EXISTS serve_token;

DELIMITER $$

CREATE PROCEDURE serve_token(
    IN p_token_id INT
)
BEGIN
    DECLARE v_status VARCHAR(20);
    DECLARE v_staff_id INT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    SELECT status, staff_id
    INTO v_status, v_staff_id
    FROM Token
    WHERE token_id = p_token_id
    FOR UPDATE;

    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Token does not exist';
    END IF;

    IF v_status != 'called' THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Only called tokens can be marked serving';
    END IF;

    UPDATE Token
    SET status = 'serving',
        served_at = CURRENT_TIMESTAMP
    WHERE token_id = p_token_id;

    INSERT INTO Queue_History (token_id, previous_status, new_status, changed_by)
    VALUES (p_token_id, 'called', 'serving', v_staff_id);

    COMMIT;
END$$

DELIMITER ;