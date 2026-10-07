-- QueueX Demo Seed Data
-- 2 Branches, 3 Services, 2 Queues, 5 Counters, 3 Staff, 5 Customers

-- ============================================================
-- 1. BRANCHES
-- ============================================================

INSERT INTO `Branch`
    (`branch_id`, `name`, `address`, `phone`)
VALUES
    (1, 'Main Branch', 'MG Road, Pune', '020-40000001'),
    (2, 'City Branch', 'FC Road, Pune', '020-40000002');

-- ============================================================
-- 2. SERVICES
-- ============================================================

INSERT INTO `Service`
    (`service_id`, `name`, `description`, `estimated_duration_minutes`)
VALUES
    (1, 'Account Services', 'Account opening, closing and account-related requests', 15),
    (2, 'Cash Services', 'Cash deposit and withdrawal services', 10),
    (3, 'Loan Services', 'Loan enquiries and applications', 20);

-- ============================================================
-- 3. QUEUES
-- ============================================================

INSERT INTO `Queue`
    (`queue_id`, `branch_id`, `service_id`, `name`, `is_active`, `last_token_number`)
VALUES
    (1, 1, 1, 'Main Branch - Account Services', TRUE, 0),
    (2, 2, 2, 'City Branch - Cash Services', TRUE, 0);

-- ============================================================
-- 4. COUNTERS
-- ============================================================

INSERT INTO `Counter`
    (`counter_id`, `branch_id`, `queue_id`, `name`, `location`, `floor`, `is_active`)
VALUES
    (1, 1, 1, 'Counter 1', 'Main Hall', 'Ground', TRUE),
    (2, 1, 1, 'Counter 2', 'Main Hall', 'Ground', TRUE),
    (3, 1, NULL, 'Counter 3', 'Main Hall', 'Ground', TRUE),
    (4, 2, 2, 'Counter 1', 'Main Hall', 'Ground', TRUE),
    (5, 2, 2, 'Counter 2', 'Main Hall', 'Ground', TRUE);

-- ============================================================
-- 5. STAFF
-- ============================================================

INSERT INTO `Staff`
    (`staff_id`, `branch_id`, `username`, `password_hash`, `full_name`, `email`, `role`, `is_active`)
VALUES
    (
        1,
        1,
        'admin',
        SHA2('admin123', 256),
        'QueueX Administrator',
        'admin@queuex.local',
        'admin',
        TRUE
    ),
    (
        2,
        1,
        'staff1',
        SHA2('staff123', 256),
        'Main Branch Staff',
        'staff1@queuex.local',
        'staff',
        TRUE
    ),
    (
        3,
        2,
        'staff2',
        SHA2('staff123', 256),
        'City Branch Staff',
        'staff2@queuex.local',
        'staff',
        TRUE
    );

-- ============================================================
-- 6. CUSTOMERS
-- ============================================================

INSERT INTO `Customer`
    (`customer_id`, `full_name`, `phone`, `email`)
VALUES
    (1, 'Aarav Sharma', '9000000001', 'aarav@example.com'),
    (2, 'Priya Patil', '9000000002', 'priya@example.com'),
    (3, 'Rohan Deshmukh', '9000000003', 'rohan@example.com'),
    (4, 'Sneha Kulkarni', '9000000004', 'sneha@example.com'),
    (5, 'Vikram Joshi', '9000000005', 'vikram@example.com');