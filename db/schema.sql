-- QueueX Database Schema
-- MySQL 8.0+ / PostgreSQL 14+ compatible
-- Run: mysql -u root -p queuex < db/schema.sql
-- Or: psql -U postgres -d queuex -f db/schema.sql

-- Drop tables in reverse dependency order (for idempotent re-runs)
DROP TABLE IF EXISTS `Notification`;
DROP TABLE IF EXISTS `Queue_History`;
DROP TABLE IF EXISTS `Token`;
DROP TABLE IF EXISTS `Customer`;
DROP TABLE IF EXISTS `Staff`;
DROP TABLE IF EXISTS `Counter`;
DROP TABLE IF EXISTS `Queue`;
DROP TABLE IF EXISTS `Service`;
DROP TABLE IF EXISTS `Branch`;

-- ============================================================
-- 1. BRANCH
-- ============================================================
CREATE TABLE `Branch` (
    `branch_id` INT NOT NULL AUTO_INCREMENT,
    `name` VARCHAR(100) NOT NULL,
    `address` VARCHAR(255),
    `phone` VARCHAR(20),
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`branch_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 2. SERVICE
-- ============================================================
CREATE TABLE `Service` (
    `service_id` INT NOT NULL AUTO_INCREMENT,
    `name` VARCHAR(100) NOT NULL,
    `description` TEXT,
    `estimated_duration_minutes` INT DEFAULT 15,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`service_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 3. QUEUE
-- ============================================================
CREATE TABLE `Queue` (
    `queue_id` INT NOT NULL AUTO_INCREMENT,
    `branch_id` INT NOT NULL,
    `service_id` INT NOT NULL,
    `name` VARCHAR(100) NOT NULL,
    `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
    `last_token_number` INT NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`queue_id`),
    CONSTRAINT `fk_queue_branch` FOREIGN KEY (`branch_id`) REFERENCES `Branch`(`branch_id`)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT `fk_queue_service` FOREIGN KEY (`service_id`) REFERENCES `Service`(`service_id`)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE KEY `uk_queue_branch_service` (`branch_id`, `service_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 4. COUNTER
-- ============================================================
CREATE TABLE `Counter` (
    `counter_id` INT NOT NULL AUTO_INCREMENT,
    `branch_id` INT NOT NULL,
    `queue_id` INT,
    `name` VARCHAR(50) NOT NULL,
    `location` VARCHAR(100),
    `floor` VARCHAR(20),
    `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`counter_id`),
    CONSTRAINT `fk_counter_branch` FOREIGN KEY (`branch_id`) REFERENCES `Branch`(`branch_id`)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT `fk_counter_queue` FOREIGN KEY (`queue_id`) REFERENCES `Queue`(`queue_id`)
        ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 5. STAFF
-- ============================================================
CREATE TABLE `Staff` (
    `staff_id` INT NOT NULL AUTO_INCREMENT,
    `branch_id` INT NOT NULL,
    `username` VARCHAR(50) NOT NULL,
    `password_hash` VARCHAR(255) NOT NULL,
    `full_name` VARCHAR(100) NOT NULL,
    `email` VARCHAR(100),
    `role` ENUM('admin', 'staff') NOT NULL DEFAULT 'staff',
    `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`staff_id`),
    CONSTRAINT `fk_staff_branch` FOREIGN KEY (`branch_id`) REFERENCES `Branch`(`branch_id`)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE KEY `uk_staff_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 6. CUSTOMER
-- ============================================================
CREATE TABLE `Customer` (
    `customer_id` INT NOT NULL AUTO_INCREMENT,
    `full_name` VARCHAR(100) NOT NULL,
    `phone` VARCHAR(20),
    `email` VARCHAR(100),
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`customer_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 7. TOKEN
-- ============================================================
CREATE TABLE `Token` (
    `token_id` INT NOT NULL AUTO_INCREMENT,
    `queue_id` INT NOT NULL,
    `customer_id` INT NOT NULL,
    `token_number` INT NOT NULL,
    `status` ENUM('waiting', 'called', 'serving', 'completed', 'no_show') NOT NULL DEFAULT 'waiting',
    `counter_id` INT,
    `staff_id` INT,
    `issued_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `called_at` DATETIME,
    `served_at` DATETIME,
    `completed_at` DATETIME,
    PRIMARY KEY (`token_id`),
    CONSTRAINT `fk_token_queue` FOREIGN KEY (`queue_id`) REFERENCES `Queue`(`queue_id`)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT `fk_token_customer` FOREIGN KEY (`customer_id`) REFERENCES `Customer`(`customer_id`)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT `fk_token_counter` FOREIGN KEY (`counter_id`) REFERENCES `Counter`(`counter_id`)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT `fk_token_staff` FOREIGN KEY (`staff_id`) REFERENCES `Staff`(`staff_id`)
        ON UPDATE CASCADE ON DELETE SET NULL,
    UNIQUE KEY `uk_token_queue_number` (`queue_id`, `token_number`),
    INDEX `idx_token_queue_status` (`queue_id`, `status`),
    INDEX `idx_token_customer` (`customer_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 8. QUEUE_HISTORY (audit trail - append only)
-- ============================================================
CREATE TABLE `Queue_History` (
    `history_id` INT NOT NULL AUTO_INCREMENT,
    `token_id` INT NOT NULL,
    `previous_status` ENUM('waiting', 'called', 'serving', 'completed', 'no_show'),
    `new_status` ENUM('waiting', 'called', 'serving', 'completed', 'no_show') NOT NULL,
    `changed_by` INT,
    `changed_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`history_id`),
    CONSTRAINT `fk_history_token` FOREIGN KEY (`token_id`) REFERENCES `Token`(`token_id`)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT `fk_history_staff` FOREIGN KEY (`changed_by`) REFERENCES `Staff`(`staff_id`)
        ON UPDATE CASCADE ON DELETE SET NULL,
    INDEX `idx_history_token_time` (`token_id`, `changed_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 9. NOTIFICATION
-- ============================================================
CREATE TABLE `Notification` (
    `notification_id` INT NOT NULL AUTO_INCREMENT,
    `customer_id` INT NOT NULL,
    `token_id` INT,
    `channel` ENUM('sms', 'push', 'in_app', 'email') NOT NULL DEFAULT 'in_app',
    `message` TEXT NOT NULL,
    `status` ENUM('pending', 'sent', 'failed') NOT NULL DEFAULT 'pending',
    `sent_at` DATETIME,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`notification_id`),
    CONSTRAINT `fk_notification_customer` FOREIGN KEY (`customer_id`) REFERENCES `Customer`(`customer_id`)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT `fk_notification_token` FOREIGN KEY (`token_id`) REFERENCES `Token`(`token_id`)
        ON UPDATE CASCADE ON DELETE SET NULL,
    INDEX `idx_notification_customer` (`customer_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;