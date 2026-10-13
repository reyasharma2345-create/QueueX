-- QueueX Phase 1: Transaction-safe token creation
-- Apply only to queuex_test during development.
-- Do not add USE queuex here.

DROP PROCEDURE IF EXISTS issue_token;
DROP PROCEDURE IF EXISTS create_token_internal;

DELIMITER $$

CREATE PROCEDURE create_token_internal(
    IN p_queue_id INT,
    IN p_customer_id INT,
    OUT p_token_id INT,
    OUT p_token_number INT
)
BEGIN
    DECLARE v_last_token INT DEFAULT NULL;
    DECLARE v_is_active BOOLEAN DEFAULT NULL;

    SELECT last_token_number, is_active
    INTO v_last_token, v_is_active
    FROM Queue
    WHERE queue_id = p_queue_id
    FOR UPDATE;

    IF v_last_token IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Queue does not exist';
    END IF;

    IF v_is_active = FALSE THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Queue is not active';
    END IF;

    SET p_token_number = v_last_token + 1;

    UPDATE Queue
    SET last_token_number = p_token_number
    WHERE queue_id = p_queue_id;

    INSERT INTO Token (
        queue_id, customer_id, token_number, status
    )
    VALUES (
        p_queue_id, p_customer_id, p_token_number, 'waiting'
    );

    SET p_token_id = LAST_INSERT_ID();

    INSERT INTO Queue_History (
        token_id, previous_status, new_status, changed_by
    )
    VALUES (p_token_id, NULL, 'waiting', NULL);
END$$

CREATE PROCEDURE issue_token(
    IN p_queue_id INT,
    IN p_customer_id INT,
    OUT p_token_id INT,
    OUT p_token_number INT
)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    CALL create_token_internal(
        p_queue_id, p_customer_id, p_token_id, p_token_number
    );

    COMMIT;
END$$

DELIMITER ;
