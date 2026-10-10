
USE queuex;

-- =====================================================
-- QueueX: Customer Accounts and Service Requests
-- =====================================================



-- 2. CUSTOMER ACCOUNT
CREATE TABLE IF NOT EXISTS customer_account (
    account_id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL UNIQUE,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_customer_account_customer
        FOREIGN KEY (customer_id)
        REFERENCES customer(customer_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
) ENGINE=InnoDB;


-- 3. DOCUMENT MASTER
CREATE TABLE IF NOT EXISTS document (
    document_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(120) NOT NULL UNIQUE,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;


-- 4. SERVICE DOCUMENT
-- Defines which documents are required for each service.
CREATE TABLE IF NOT EXISTS service_document (
    service_id INT NOT NULL,
    document_id INT NOT NULL,
    is_required BOOLEAN NOT NULL DEFAULT TRUE,
    instructions TEXT,

    PRIMARY KEY (service_id, document_id),

    CONSTRAINT fk_service_document_service
        FOREIGN KEY (service_id)
        REFERENCES service(service_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,

    CONSTRAINT fk_service_document_document
        FOREIGN KEY (document_id)
        REFERENCES document(document_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
) ENGINE=InnoDB;


-- 5. CUSTOMER REQUEST
-- Each request belongs to one specific branch/service queue.
CREATE TABLE IF NOT EXISTS customer_request (
    request_id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    queue_id INT NOT NULL,
    token_id INT UNIQUE DEFAULT NULL,

    request_status ENUM(
        'draft',
        'ready',
        'queued',
        'in_progress',
        'completed',
        'cancelled'
    ) NOT NULL DEFAULT 'draft',

    readiness_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_request_queue (request_id, queue_id),

    INDEX idx_request_customer (customer_id),
    INDEX idx_request_queue_status (queue_id, request_status),

    CONSTRAINT fk_customer_request_customer
        FOREIGN KEY (customer_id)
        REFERENCES customer(customer_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,

    CONSTRAINT fk_customer_request_queue
        FOREIGN KEY (queue_id)
        REFERENCES queue(queue_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,

    CONSTRAINT fk_customer_request_token
        FOREIGN KEY (token_id)
        REFERENCES token(token_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
) ENGINE=InnoDB;



-- 6. REQUEST DOCUMENT
-- Tracks document readiness for each customer request.

CREATE TABLE IF NOT EXISTS request_document (
    request_id INT NOT NULL,
    document_id INT NOT NULL,

    readiness_status ENUM(
        'pending',
        'available',
        'missing'
    ) NOT NULL DEFAULT 'pending',

    confirmed_at DATETIME DEFAULT NULL,

    PRIMARY KEY (request_id, document_id),

    CONSTRAINT fk_request_document_request
        FOREIGN KEY (request_id)
        REFERENCES customer_request(request_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,

    CONSTRAINT fk_request_document_service_document
        FOREIGN KEY (document_id)
        REFERENCES document(document_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
) ENGINE=InnoDB;